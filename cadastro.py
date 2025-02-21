# cadastro.py
import asyncio
import logging
import os
import time
import subprocess  # Adicionar este
import sys        # Adicionar este
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime

# Configuração do logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Token do bot de cadastro
from config import ADMIN_BOT_TOKEN
from config import ADMIN_BOT_TOKEN, ADMIN_USER_ID
class BotState(StatesGroup):
    awaiting_token = State()

class Database:
    def __init__(self):
        self.db_file = "bots.db"
        self.init_db()

    def init_db(self):
        """Inicializa o banco de dados com a tabela necessária"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Cria a tabela de bots se não existir
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bots (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                bot_id INTEGER NOT NULL,
                bot_username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                gateway_token TEXT,
                last_activity TEXT,
                mp_refresh_token TEXT,
                mp_user_id TEXT,
                gateway_type TEXT DEFAULT 'pushinpay'
            )
        ''')
        
        conn.commit()
        conn.close()

    def load_bots(self):
        """Carrega todos os bots do banco de dados"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM bots')
        rows = cursor.fetchall()
        
        bots = []
        for row in rows:
            bot = {
                'token': row[0],
                'user_id': row[1],
                'bot_id': row[2],
                'bot_username': row[3],
                'created_at': row[4],
                'is_active': bool(row[5]),
                'gateway_token': row[6]
            }
            bots.append(bot)
            
        conn.close()
        return bots

    def save_bot(self, bot_data):
        """Salva um novo bot no banco de dados"""
        print(f"\n💾 Iniciando salvamento do bot: {bot_data['bot_username']}")
        
        for attempt in range(5):
            try:
                # Primeiro limpa qualquer registro antigo
                self.clean_old_bot(bot_data['token'])
                
                conn = sqlite3.connect(self.db_file, timeout=20)
                cursor = conn.cursor()
                
                # Força o modo WAL para melhor concorrência
                cursor.execute('PRAGMA journal_mode=WAL')
                
                # Insere o novo registro
                cursor.execute('''
                    INSERT INTO bots (
                        token, user_id, bot_id, bot_username, 
                        created_at, is_active, gateway_token,
                        last_activity, mp_refresh_token, mp_user_id, 
                        gateway_type
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 'pushinpay')
                ''', (
                    bot_data['token'],
                    bot_data['user_id'],
                    bot_data['bot_id'],
                    bot_data['bot_username'],
                    bot_data['created_at'],
                    bot_data['is_active']
                ))
                
                conn.commit()
                print(f"✅ Bot salvo com sucesso")
                return True
                
            except sqlite3.OperationalError as e:
                if 'database is locked' in str(e):
                    print(f"⚠️ Banco bloqueado, tentativa {attempt + 1}/5")
                    time.sleep(1)
                    continue
                raise e
            except Exception as e:
                print(f"❌ Erro ao salvar bot: {str(e)}")
                raise e
            finally:
                try:
                    conn.close()
                except:
                    pass
        
        raise Exception("Não foi possível salvar o bot após 5 tentativas")

    def get_user_bots(self, user_id):
        """Obtém todos os bots de um usuário específico"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM bots WHERE user_id = ?', (user_id,))
        rows = cursor.fetchall()
        
        bots = []
        for row in rows:
            bot = {
                'token': row[0],
                'user_id': row[1],
                'bot_id': row[2],
                'bot_username': row[3],
                'created_at': row[4],
                'is_active': bool(row[5]),
                'gateway_token': row[6]
            }
            bots.append(bot)
            
        conn.close()
        return bots
    
    def clean_old_bot(self, token: str) -> bool:
        """Limpa completamente registros antigos de um bot"""
        print(f"\n🧹 Iniciando limpeza completa do bot: {token}")
        
        try:
            conn = sqlite3.connect(self.db_file, isolation_level=None)
            cursor = conn.cursor()
            
            # Desativa o bot primeiro
            cursor.execute('''
                UPDATE bots 
                SET is_active = 0 
                WHERE token = ?
            ''', (token,))
            
            # Remove da tabela de bots
            cursor.execute('DELETE FROM bots WHERE token = ?', (token,))
            
            # Remove da tabela de processos
            cursor.execute('DELETE FROM bot_processes WHERE token = ?', (token,))
            
            conn.commit()
            print("✅ Registros antigos removidos com sucesso")
            return True
            
        except Exception as e:
            print(f"❌ Erro ao limpar registros antigos: {str(e)}")
            return False
        finally:
            try:
                conn.close()
            except:
                pass

    def bot_exists(self, token: str) -> bool:
        """Verifica se um bot já existe e está ativo no banco de dados"""
        print(f"\n🔍 Verificando existência do bot com token: {token}")
        
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Verifica se o bot existe e está ativo
            cursor.execute('''
                SELECT COUNT(*), is_active 
                FROM bots 
                WHERE token = ?
            ''', (token,))
            
            result = cursor.fetchone()
            
            if result and result[0] > 0:
                is_active = bool(result[1])
                
                if is_active:
                    print("⚠️ Bot encontrado e está ativo")
                    return True
                else:
                    print("✅ Bot encontrado mas está inativo - permitindo recadastro")
                    # Limpa registros antigos para permitir recadastro
                    cursor.execute('DELETE FROM bots WHERE token = ?', (token,))
                    cursor.execute('DELETE FROM bot_processes WHERE token = ?', (token,))
                    conn.commit()
                    return False
            
            print("✅ Bot não existe no sistema")
            return False
                
        except Exception as e:
            print(f"❌ Erro ao verificar existência do bot: {str(e)}")
            return False
            
        finally:
            try:
                conn.close()
            except:
                pass
            
    def sync_bot_records(self):
        """Sincroniza os registros de bots e processos"""
        try:
            conn = sqlite3.connect(self.db_file, isolation_level=None)
            cursor = conn.cursor()
            
            # Remove processos sem bots
            cursor.execute('''
                DELETE FROM bot_processes 
                WHERE token NOT IN (SELECT token FROM bots)
            ''')
            
            # Desativa bots sem processos
            cursor.execute('''
                UPDATE bots 
                SET is_active = 0 
                WHERE token NOT IN (SELECT token FROM bot_processes)
            ''')
            
            # Remove bots inativos
            cursor.execute('DELETE FROM bots WHERE is_active = 0')
            
            conn.commit()
        except Exception as e:
            print(f"❌ Erro ao sincronizar registros: {str(e)}")
        finally:
            try:
                conn.close()
            except:
                pass

class CadastroBot:
    def __init__(self):
        self.bot = Bot(token=ADMIN_BOT_TOKEN)
        self.dp = Dispatcher()
        self.db = Database()
        self.setup_handlers()

    def setup_handlers(self):
        self.dp.message.register(self.start_command, CommandStart())
        self.dp.message.register(self.process_token, BotState.awaiting_token)
        self.dp.message.register(self.handle_message, F.text)
        self.dp.callback_query.register(self.handle_callback)

    async def start_command(self, message: Message, state: FSMContext):
        # Limpa qualquer estado anterior
        await state.clear()

        # Cria um teclado inline com o novo layout
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(text="🤖 Cadastrar Novo Bot", callback_data="register_bot"))
        builder.add(InlineKeyboardButton(text="📋 Meus Bots Cadastrados", callback_data="list_bots"))
        builder.row(InlineKeyboardButton(text="💰 Taxas", callback_data="fees"))
        builder.add(InlineKeyboardButton(text="❓ Ajuda", callback_data="help"))
        builder.add(InlineKeyboardButton(text="📜 Termos de Uso", callback_data="terms"))
        
        # Ajusta o layout: 1 botão por linha para os dois primeiros, 2 botões na terceira linha, 1 na última
        builder.adjust(1, 1, 2, 1)

        await message.answer(
            "👋 *Bem-vindo ao Sistema de Cadastro de Bots PIX!*\n\n"
            "O que você deseja fazer?",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )

    async def handle_message(self, message: Message, state: FSMContext):
        text = message.text

        if text == "Enviar Token":
            await message.answer(
                "📝 *Instruções para Cadastro de Bot*\n\n"
                "1. Crie um novo bot no BotFather (@BotFather)\n"
                "2. Copie o token do bot\n"
                "3. Cole o token aqui para cadastrar\n\n",
                parse_mode="Markdown"
            )
            await state.set_state(BotState.awaiting_token)

    async def handle_callback(self, callback: CallbackQuery, state: FSMContext):
        # Obtém o dado do callback
        data = callback.data

        # Responde ao callback para remover o "carregando"
        await callback.answer()

        if data == "register_bot":
            # Limpa qualquer estado anterior
            await state.clear()

            # Instruções para cadastro de bot
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="❌ Cancelar", callback_data="start"))
            
            await callback.message.edit_text(
                "📝 *Instruções para Cadastro de Bot*\n\n"
                "1. Crie um novo bot no @BotFather\n"
                "2. Copie o token do bot\n"
                "3. Cole o token aqui para cadastrar\n\n",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
            
            await state.set_state(BotState.awaiting_token)

        elif data == "list_bots":
            # Lista os bots do usuário
            user_bots = self.db.get_user_bots(callback.from_user.id)
            
            builder = InlineKeyboardBuilder()
            
            # Cria um botão para cada bot cadastrado
            if user_bots:
                for bot in user_bots:
                    builder.add(InlineKeyboardButton(
                        text=f"{bot['bot_username']}", 
                        callback_data="bot_info_disabled"
                    ))
            
            # Adiciona botão de voltar
            builder.row(InlineKeyboardButton(text="🏠 Voltar", callback_data="start"))
            
            # Ajusta para ter um botão por linha
            builder.adjust(1)

            await callback.message.edit_text(
                "🤖 *Seus Bots Cadastrados*\n\n" + 
                ("Nenhum bot cadastrado ainda." if not user_bots else ""),
                parse_mode="Markdown", 
                reply_markup=builder.as_markup()
            )

        elif data == "fees":
            # Informações sobre taxas
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🏠 Voltar", callback_data="start"))

            await callback.message.edit_text(
                "💰 *Taxas de Serviço*\n\n"
                "Nosso sistema tem uma taxa simples e transparente para garantir *qualidade* e *segurança dos usuários.*\n\n"
                "• *Taxa por transação:* 3%\n\n"
                "⚙️ *Por que cobramos isso?* A taxa cobre custos de servidor, garantindo uma experiência rápida e segura.\n\n"
                "A taxa é automaticamente descontada de cada transação.",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )

        elif data == "help":
            # Mensagem de ajuda atualizada
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🏠 Voltar", callback_data="start"))

            await callback.message.edit_text(
                "🙋 *Ajuda - Sistema de Cadastro*\n\n"
                "• *Cadastrar Novo Bot*: Adicione um novo bot ao sistema.\n\n"
                "• *Meus Bots*: Veja todos os seus bots cadastrados.\n\n"
                "• *Taxas*: Consulte informações sobre custos.\n\n"
                "• *Termos de Uso*: Leia as regras e condições.\n\n"
                "Precisa de suporte? Entre em contato com o administrador.",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )

        elif data == "terms":
            # Termos de uso
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🏠 Voltar", callback_data="start"))

            await callback.message.edit_text(
                "📜 *Termos de Uso*\n\n"
                "Ao utilizar nosso serviço, você concorda com os seguintes termos:\n\n"
                "1. *Responsabilidade*\n\n"
                "• O usuário é responsável por todas as transações realizadas\n"
                "• O bot deve ser usado apenas para fins legais\n\n"
                "2. *Uso do Serviço*\n\n"
                "• É proibido usar o bot para atividades ilícitas\n"
                "• O serviço pode ser suspenso em caso de uso inadequado\n\n"
                "3. *Taxas e Pagamentos*\n\n"
                "• Taxa de 3% por transação\n"
                "• Valores são processados conforme regras do PIX\n\n"
                "4. *Privacidade*\n\n"
                "• Seus dados são protegidos e não compartilhados\n"
                "• Transações são processadas de forma segura\n\n"
                "5. *Modificações*\n\n"
                "• Os termos podem ser atualizados a qualquer momento\n"
                "• Usuários serão notificados sobre mudanças importantes",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )

        elif data == "start":
            # Limpa qualquer estado anterior
            await state.clear()

            # Volta para o início
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🤖 Cadastrar Novo Bot", callback_data="register_bot"))
            builder.add(InlineKeyboardButton(text="📋 Meus Bots Cadastrados", callback_data="list_bots"))
            builder.row(InlineKeyboardButton(text="💰 Taxas", callback_data="fees"))
            builder.add(InlineKeyboardButton(text="❓ Ajuda", callback_data="help"))
            builder.add(InlineKeyboardButton(text="📜 Termos de Uso", callback_data="terms"))
            builder.adjust(1, 1, 2, 1)

            await callback.message.edit_text(
                "👋 *Bem-vindo ao Sistema de Cadastro de Bots PIX!*\n\n"
                "O que você deseja fazer?",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
        
        elif data == "bot_info_disabled":
            await callback.answer("Nenhuma ação disponível para este bot.", show_alert=True)

    async def process_token(self, message: Message, state: FSMContext):
        """Processa o token enviado pelo usuário"""
        if message.text.startswith('/'):
            await state.clear()
            return
        
        token = message.text.strip()
        
        if not self.validate_token(token):
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🔄 Tentar Novamente", callback_data="register_bot"))
            
            await message.answer(
                "❌ *Token Inválido*\n"
                "O token deve estar no formato:\n"
                "`123456:ABCdefGHIjklMNOpqrSTUvwxYZ`",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
            await state.clear()
            return

        # Verifica se o bot já está cadastrado e ativo
        if self.db.bot_exists(token):
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🔄 Tentar Novamente", callback_data="register_bot"))
            builder.add(InlineKeyboardButton(text="🏠 Voltar ao Início", callback_data="start"))
            builder.adjust(1)
            
            await message.answer(
                "⚠️ *O Token já está cadastrado no sistema!* Tente novamente com outro.\n\n",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
            await state.clear()
            return

        try:
            # Otimizado: Apenas uma verificação inicial rápida
            temp_bot = Bot(token=token)
            await temp_bot.delete_webhook(drop_pending_updates=True)
            await temp_bot.session.close()
            await asyncio.sleep(0.5)  # Reduzido para 0.5 segundos

            # Testa o bot e obtém informações
            test_bot = Bot(token=token)
            bot_info = await test_bot.get_me()
            await test_bot.session.close()

            new_bot = {
                "user_id": message.from_user.id,
                "bot_id": bot_info.id,
                "bot_username": bot_info.username,
                "token": token,
                "created_at": datetime.utcnow().isoformat(),
                "is_active": True
            }

            # Salva o bot no banco de dados
            self.db.save_bot(new_bot)
            
            # Envia notificação para o administrador
            try:
                user = message.from_user
                admin_notification = (
                    "✅ *Novo Bot Cadastrado*\n\n"
                    f"👤 *Cliente:*\n"
                    f"• Nome: {user.first_name}"
                    f"{(' ' + user.last_name) if user.last_name else ''}\n"
                    f"• Username: @{user.username if user.username else 'Sem username'}\n"
                    f"• ID: `{user.id}`\n\n"
                    f"🤖 *Bot:*\n"
                    f"• Nome: {bot_info.first_name}\n"
                    f"• Username: @{bot_info.username}\n"
                    f"• ID: `{bot_info.id}`\n\n"
                    f"📅 Data: {datetime.utcnow().strftime('%d/%m/%Y %H:%M:%S')}"
                )
                
                await self.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text=admin_notification,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Erro ao enviar notificação para admin: {e}")

            # Inicia o processo do bot
            subprocess.Popen([sys.executable, "app.py", token])
            await asyncio.sleep(3)  # Reduzido para 1 segundo
            
            # Prepara a resposta
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🤖 Acessar Meu Bot", url=f"https://t.me/{bot_info.username}"))
            builder.add(InlineKeyboardButton(text="🏠 Voltar ao Início", callback_data="start"))
            builder.adjust(1)

            # Envia mensagem de sucesso
            await message.answer(
                f"✅ *Bot Cadastrado com Sucesso!*\n\n"
                "1. Vá até o bot e dê o comando /start\n"
                "2. Configure seu gateway PIX\n"
                "3. Comece a gerar pagamentos",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
            
            # Apaga a mensagem com o token por segurança
            try:
                await message.delete()
            except Exception as e:
                print(f"⚠️ Erro ao tentar apagar mensagem com token: {e}")
                
        except Exception as e:
            logger.error(f"Erro ao cadastrar bot: {e}")
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🔄 Tentar Novamente", callback_data="register_bot"))
            
            await message.answer(
                "❌ *Erro no Cadastro*\n"
                "Não foi possível validar o token.\n"
                "Verifique se o token está correto.",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )

        finally:
            await state.clear()

    @staticmethod
    def validate_token(token: str) -> bool:
        parts = token.split(":")
        return (
            len(parts) == 2 and
            parts[0].isdigit() and
            len(parts[1]) >= 30
        )

    async def start(self):
        try:
            await self.bot.delete_webhook(drop_pending_updates=True)
            await self.dp.start_polling(self.bot)
        finally:
            await self.bot.session.close()

if __name__ == "__main__":
    cadastro_bot = CadastroBot()
    asyncio.run(cadastro_bot.start())