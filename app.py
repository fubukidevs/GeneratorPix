# app.py
import asyncio
import json
import logging
import os
import signal
import time
import psutil
import sys
import uuid  # Adicione este import no topo do arquivo
import mercadopago
import sqlite3
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiohttp
from datetime import datetime, timedelta

from config import (
    MP_CLIENT_ID,
    MP_CLIENT_SECRET,
    MP_REDIRECT_URI,
    PUSHINPAY_BASE_URL,
    PUSHINPAY_ACCOUNT_ID,
    INACTIVITY_MINUTES
)

# Configuração do FSM
class TokenState(StatesGroup):
    selecting_gateway = State()  # Novo estado para seleção do gateway
    waiting_token = State()
    waiting_pix_value = State()
    waiting_mp_token = State()  # Novo estado para token do MP

# Configurações
PUSHINPAY_BASE_URL = "https://api.pushinpay.com.br/api/pix/cashIn"

# Configuração do logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.db_file = "bots.db"
        self.init_db()

    def init_db(self):
        """Inicializa o banco de dados com a tabela necessária"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Adiciona a coluna is_public se ela não existir
        try:
            cursor.execute('ALTER TABLE bots ADD COLUMN is_public BOOLEAN DEFAULT 0')
            conn.commit()
        except sqlite3.OperationalError:
            # Coluna já existe
            pass
        
        conn.close()
        
    def update_bot_access(self, bot_token: str, is_public: bool):
        """Atualiza a configuração de acesso público do bot"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE bots 
            SET is_public = ?
            WHERE token = ?
        ''', (is_public, bot_token))
        
        conn.commit()
        conn.close()
        
    def is_bot_public(self, bot_token: str) -> bool:
        """Verifica se o bot está configurado como público"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT is_public FROM bots WHERE token = ?', (bot_token,))
        result = cursor.fetchone()
        
        conn.close()
        return bool(result[0]) if result else False
        
    def save_mp_credentials(self, bot_token: str, access_token: str, refresh_token: str, user_id: str):
        """Salva as credenciais do Mercado Pago para um bot específico"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Log para debug
            print(f"\nSalvando credenciais MP para bot: {bot_token}")
            print(f"Access Token (primeiros 10 chars): {access_token[:10]}...")
            
            cursor.execute('''
                UPDATE bots 
                SET gateway_token = ?,
                    mp_refresh_token = ?,
                    mp_user_id = ?,
                    gateway_type = 'mercadopago'
                WHERE token = ?
            ''', (access_token, refresh_token, user_id, bot_token))
            
            # Verifica se o update funcionou
            if cursor.rowcount == 0:
                print("⚠️ Nenhuma linha foi atualizada!")
                
                # Verifica se o bot existe
                cursor.execute('SELECT COUNT(*) FROM bots WHERE token = ?', (bot_token,))
                count = cursor.fetchone()[0]
                print(f"Bots encontrados com este token: {count}")
            else:
                print("✅ Credenciais atualizadas com sucesso")
            
            conn.commit()
        except Exception as e:
            print(f"❌ Erro ao salvar credenciais MP: {str(e)}")
            raise e
        finally:
            conn.close()
        
    def get_gateway_type(self, bot_token: str) -> str:
        """Obtém o tipo de gateway para um bot específico"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT gateway_type FROM bots WHERE token = ?', (bot_token,))
        result = cursor.fetchone()
        
        conn.close()
        return result[0] if result else "pushinpay"  # Padrão para pushinpay se não encontrar


    def get_mp_credentials(self, bot_token: str):
        """Obtém as credenciais do Mercado Pago para um bot específico"""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            
            # Log para debug
            print(f"\nBuscando credenciais MP para bot: {bot_token}")
            
            cursor.execute('''
                SELECT gateway_token, mp_refresh_token, mp_user_id, gateway_type
                FROM bots 
                WHERE token = ?
            ''', (bot_token,))
            
            result = cursor.fetchone()
            
            if result:
                print(f"Gateway type encontrado: {result[3]}")
                print(f"Token encontrado: {result[0][:10] if result[0] else 'None'}...")
                
                if result[3] == 'mercadopago':
                    return {
                        'access_token': result[0],
                        'refresh_token': result[1],
                        'user_id': result[2]
                    }
            else:
                print("⚠️ Nenhuma credencial encontrada para este bot")
                
            return None
            
        except Exception as e:
            print(f"❌ Erro ao buscar credenciais MP: {str(e)}")
            return None
        finally:
            conn.close()
    
    def update_gateway_type(self, bot_token: str, gateway_type: str):
        """Atualiza o tipo de gateway no banco de dados"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE bots 
            SET gateway_type = ?
            WHERE token = ?
        ''', (gateway_type, bot_token))
        
        conn.commit()
        conn.close()

    def save_pid(self, token: str, pid: int):
        """Salva o PID do processo do bot"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO bot_processes (token, pid)
            VALUES (?, ?)
        ''', (token, pid))
        
        conn.commit()
        conn.close()

    def get_owner_id(self, token: str) -> int:
        """Obtém o ID do dono do bot do banco de dados"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id FROM bots WHERE token = ?', (token,))
        result = cursor.fetchone()
        
        conn.close()
        return result[0] if result else None

    def get_gateway_token(self, bot_token: str) -> str:
        """Obtém o token do gateway do banco de dados"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Primeiro verifica qual é o tipo de gateway
        cursor.execute('SELECT gateway_type, gateway_token FROM bots WHERE token = ?', (bot_token,))
        result = cursor.fetchone()
        
        conn.close()
        
        if not result:
            return None
            
        gateway_type, token = result
        
        # Se for Mercado Pago, verifica se tem credenciais válidas
        if gateway_type == 'mercadopago':
            mp_credentials = self.get_mp_credentials(bot_token)
            if mp_credentials and mp_credentials.get('access_token'):
                return mp_credentials['access_token']
            return None
        
        # Se for PushInPay, retorna o token normalmente
        return token
  
    def update_gateway_token(self, bot_token: str, gateway_token: str):
        """Atualiza o token do gateway no banco de dados"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE bots 
            SET gateway_token = ?
            WHERE token = ?
        ''', (gateway_token, bot_token))
        
        conn.commit()
        conn.close()

    def update_last_activity(self, token: str):
        """Atualiza a última atividade do bot"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE bots 
            SET last_activity = ? 
            WHERE token = ?
        ''', (datetime.utcnow().isoformat(), token))
        
        conn.commit()
        conn.close()

    def get_inactive_bots(self, minutes: int):
        """Obtém bots inativos após X minutos"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        current_time = datetime.utcnow().isoformat()
        threshold_time = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        
        # Busca bots que:
        # 1. Nunca tiveram atividade (last_activity é NULL) E foram criados há mais de X minutos
        # 2. OU tiveram última atividade há mais de X minutos
        cursor.execute('''
            SELECT * FROM bots 
            WHERE (
                (last_activity IS NULL AND created_at < ?) 
                OR 
                (last_activity < ?)
            )
            AND is_active = 1
        ''', (threshold_time, threshold_time))
        
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
                'gateway_token': row[6],
                'last_activity': row[7] if len(row) > 7 else None
            }
            bots.append(bot)
        
        conn.close()
        return bots

    def delete_bot(self, token: str):
        """Remove completamente um bot do banco de dados"""
        print(f"\n🔍 Iniciando remoção do bot com token: {token}")
        
        success = False
        for attempt in range(3):  # Tenta 3 vezes
            try:
                conn = sqlite3.connect(self.db_file, isolation_level=None)  # Auto-commit mode
                conn.execute('BEGIN IMMEDIATE')  # Lock the database
                cursor = conn.cursor()
                
                # Força limpeza de todas as tabelas
                cursor.execute('DELETE FROM bots WHERE token = ?', (token,))
                cursor.execute('DELETE FROM bot_processes WHERE token = ?', (token,))
                
                # Força o commit
                conn.commit()
                
                # Verifica se foi realmente removido
                cursor.execute('SELECT COUNT(*) FROM bots WHERE token = ?', (token,))
                bots_count = cursor.fetchone()[0]
                
                cursor.execute('SELECT COUNT(*) FROM bot_processes WHERE token = ?', (token,))
                processes_count = cursor.fetchone()[0]
                
                if bots_count == 0 and processes_count == 0:
                    print(f"✅ Bot removido com sucesso na tentativa {attempt + 1}")
                    success = True
                    break
                else:
                    print(f"⚠️ Ainda existem registros: bots={bots_count}, processes={processes_count}")
                    
            except Exception as e:
                print(f"❌ Erro na tentativa {attempt + 1}: {str(e)}")
                if 'database is locked' in str(e):
                    time.sleep(1)  # Espera mais tempo se o banco estiver travado
                    continue
            finally:
                try:
                    conn.close()
                except:
                    pass
                
            if not success:
                time.sleep(0.5)
        
        if not success:
            print("❌ Falha ao remover bot após todas as tentativas")
        
        return success

class PixBot:
    def __init__(self, token: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.bot_token = token
        self.inactivity_minutes = INACTIVITY_MINUTES  # Usar valor do config.py
        self.db = Database()
        self.owner_id = self.db.get_owner_id(token)
        self.setup_handlers()
        self.save_pid()

    def setup_handlers(self):
        """Registra os handlers do bot"""
        self.dp.message.register(self.start_command, CommandStart())
        self.dp.message.register(self.pix_command, Command("pix"))
        self.dp.message.register(self.gateway_command, Command("gateway"))
        self.dp.message.register(self.livre_command, Command("livre"))  # Novo comando
        self.dp.message.register(self.process_gateway_token, TokenState.waiting_token)
        self.dp.message.register(self.process_mp_token, TokenState.waiting_mp_token)  # Novo handler
        self.dp.message.register(self.process_pix_value, TokenState.waiting_pix_value)
        self.dp.callback_query.register(self.handle_callback)

    def save_pid(self):
        """Salva o PID do processo do bot"""
        try:
            self.db.save_pid(self.bot_token, os.getpid())
        except Exception as e:
            logger.error(f"Erro ao salvar PID: {e}")
            
    async def check_permission(self, message: Message, command_type: str = "pix") -> bool:
        """
        Verifica se o usuário tem permissão para usar o comando específico
        
        Args:
            message: Mensagem do Telegram
            command_type: Tipo de comando ('pix', 'gateway', 'livre', etc)
        """
        is_owner = message.from_user.id == self.owner_id
        is_public = self.db.is_bot_public(self.bot_token)
        
        # Se for o dono, permite tudo
        if is_owner:
            return True
            
        # Se não for o dono, verifica o tipo de comando
        if command_type == "pix":
            # Apenas comando pix pode ser público
            if not is_public:
                await message.reply(
                    "⛔ *Este bot é privado*\\. Apenas o proprietário pode gerar PIX\\.\n\n"
                    "Quer um bot para gerar pix\\? Cadastre\\-se\\: @GeradorPix\\_Bot",
                    parse_mode="MarkdownV2"
                )
                return False
            return True
        else:
            # Outros comandos são sempre restritos ao dono
            return False  # Não envia mensagem aqui, pois cada comando trata isso

    async def kill_bot_process(self, bot_token: str):
        """Mata o processo do bot de forma mais robusta"""
        try:
            # 1. Força limpeza do webhook e updates pendentes
            try:
                temp_bot = Bot(token=bot_token)
                await temp_bot.delete_webhook(drop_pending_updates=True)
                await temp_bot.session.close()
                await asyncio.sleep(2)
            except Exception as e:
                print(f"⚠️ Erro ao limpar webhook: {e}")

            # 2. Mata todos os processos
            conn = sqlite3.connect(self.db.db_file)
            cursor = conn.cursor()
            cursor.execute('SELECT pid FROM bot_processes WHERE token = ?', (bot_token,))
            pids = cursor.fetchall()
            
            for pid_record in pids:
                pid = pid_record[0]
                try:
                    process = psutil.Process(pid)
                    process.terminate()
                    await asyncio.sleep(1)
                    if process.is_running():
                        process.kill()
                except psutil.NoSuchProcess:
                    pass
                except Exception as e:
                    print(f"⚠️ Erro ao matar processo {pid}: {e}")
            
            # 3. Limpa registros de processos
            cursor.execute('DELETE FROM bot_processes WHERE token = ?', (bot_token,))
            conn.commit()
            conn.close()
            
            await asyncio.sleep(2)  # Aguarda mais 2 segundos
            print(f"✅ Processo do bot finalizado com sucesso")
            
        except Exception as e:
            print(f"❌ Erro ao matar processo do bot: {e}")

# Em app.py, na classe PixBot

    async def clean_inactive_bots(self):
        """Remove bots inativos após X minutos"""
        while True:
            try:
                print("\n🔍 Verificando bots inativos...")
                
                inactive_bots = self.db.get_inactive_bots(self.inactivity_minutes)
                
                if inactive_bots:
                    print(f"⚠️ Encontrados {len(inactive_bots)} bots inativos")
                    
                    for bot in inactive_bots:
                        try:
                            print(f"\n🕒 Bot @{bot['bot_username']} inativo por {self.inactivity_minutes} minutos")
                            print(f"⏰ Última atividade: {bot['last_activity']}")
                            
                            # 1. Primeiro notifica o usuário
                            try:
                                # Importante: Usar o ADMIN_BOT_TOKEN aqui
                                admin_bot = Bot(token=self.bot_token)
                                await admin_bot.send_message(
                                    chat_id=bot["user_id"],
                                    text=f"⚠️ Seu bot foi *desativado por inatividade.* Se quiser usar novamente, basta recadastra-lo.",
                                    parse_mode="Markdown"
                                )
                                await admin_bot.session.close()
                                print("📤 Mensagem de aviso enviada para o usuário")
                            except Exception as e:
                                print(f"⚠️ Erro ao enviar mensagem: {e}")
                            
                            # 2. Depois desativa o bot no banco
                            conn = sqlite3.connect(self.db.db_file)
                            cursor = conn.cursor()
                            cursor.execute('UPDATE bots SET is_active = 0 WHERE token = ?', (bot["token"],))
                            conn.commit()
                            conn.close()
                            
                            print("✅ Bot marcado como inativo no banco")
                            
                            # 3. Força encerramento de todos os processos
                            try:
                                conn = sqlite3.connect(self.db.db_file)
                                cursor = conn.cursor()
                                cursor.execute('SELECT pid FROM bot_processes WHERE token = ?', (bot["token"],))
                                pids = cursor.fetchall()
                                conn.close()
                                
                                for pid_record in pids:
                                    pid = pid_record[0]
                                    try:
                                        process = psutil.Process(pid)
                                        process.terminate()
                                        await asyncio.sleep(1)
                                        if process.is_running():
                                            process.kill()
                                    except psutil.NoSuchProcess:
                                        pass
                                    except Exception as e:
                                        print(f"⚠️ Erro ao matar processo {pid}: {e}")
                                
                                print("✅ Processos antigos finalizados")
                            except Exception as e:
                                print(f"⚠️ Erro ao matar processos: {e}")

                            # 4. Limpa webhook
                            try:
                                temp_bot = Bot(token=bot["token"])
                                await temp_bot.delete_webhook(drop_pending_updates=True)
                                await temp_bot.session.close()
                                await asyncio.sleep(2)
                                print("✅ Webhook e sessão do bot limpos")
                            except Exception as e:
                                print(f"⚠️ Erro ao limpar webhook: {e}")

                            # 5. Remove COMPLETAMENTE do banco
                            try:
                                conn = sqlite3.connect(self.db.db_file)
                                cursor = conn.cursor()
                                
                                # Remove de todas as tabelas
                                cursor.execute('DELETE FROM bots WHERE token = ?', (bot["token"],))
                                cursor.execute('DELETE FROM bot_processes WHERE token = ?', (bot["token"],))
                                conn.commit()
                                conn.close()
                                
                                print("✅ Registros removidos do banco de dados")
                                
                                # Força um vacuum para limpar o banco
                                conn = sqlite3.connect(self.db.db_file)
                                cursor = conn.cursor()
                                cursor.execute('VACUUM')
                                conn.commit()
                                conn.close()
                                
                            except Exception as e:
                                print(f"❌ Erro ao limpar banco: {e}")

                            await asyncio.sleep(3)
                            print(f"✅ Bot @{bot['bot_username']} completamente removido do sistema")
                            
                        except Exception as e:
                            print(f"❌ Erro ao processar remoção do bot: {str(e)}")
                            logger.error(f"Erro ao remover bot: {e}")

                else:
                    print("✅ Nenhum bot inativo encontrado")

            except Exception as e:
                print(f"❌ Erro ao limpar bots inativos: {str(e)}")
                logger.error(f"Erro ao limpar bots inativos: {e}")

            print("\n⏳ Aguardando 24 horas para próxima verificação...")
            await asyncio.sleep(86400)

    async def start_command(self, message: Message):
        """Comando inicial do bot"""
        is_owner = message.from_user.id == self.owner_id
        
        # Atualiza a atividade do bot
        self.db.update_last_activity(self.bot_token)
        
        # Se for o dono, mostra menu completo
        if is_owner:
            welcome_message = (
                "*Bem\\-vindo ao Bot Gerador de PIX\\!* 🤖\n\n"
                "*/pix* 💠 \\- Gere códigos PIX copia e cola rapidamente\n\n"
                "*/gateway* ⚙️ \\- Configure seu gateway de pagamento\n\n"
                "*/livre* 🔓 \\- Configure quem pode gerar PIX no seu bot"
            )
        else:
            # Se não for o dono, verifica se o bot está público
            is_public = self.db.is_bot_public(self.bot_token)
            if is_public:
                welcome_message = (
                    "*Bem\\-vindo\\!* 🤖\n\n"
                    "*/pix* 💠 \\- Gerar novo pagamento PIX"
                )
            else:
                welcome_message = (
                    "⛔ *Este bot é privado*\\. Apenas o proprietário pode gerar PIX\\.\n\n"
                    "Quer um bot para gerar pix\\? Cadastre\\-se\\: @GeradorPix\\_Bot"
                )

        await message.reply(
            welcome_message,
            parse_mode="MarkdownV2"
        )


    async def gateway_command(self, message: Message, state: FSMContext):
        """Comando para configurar o gateway"""
        if not await self.check_permission(message, "gateway"):
            await message.reply(
                "⛔ *Este bot é privado*\\. Apenas o proprietário pode gerar PIX\\.\n\n"
                "Quer um bot para gerar pix\\? Cadastre\\-se\\: @GeradorPix\\_Bot",
                parse_mode="MarkdownV2"
            )
            return

        self.db.update_last_activity(self.bot_token)

        # Criar os botões para seleção do gateway
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(text="Mercado Pago", callback_data="select_mp"))
        builder.add(InlineKeyboardButton(text="PushinPay", callback_data="select_pushinpay"))
        builder.row(InlineKeyboardButton(text="❌ Cancelar", callback_data="cancel_gateway"))
        
        # Ajusta o layout para 2 botões na primeira linha e 1 na segunda
        builder.adjust(2, 1)

        await message.reply(
            "🔑 Qual *gateway de pagamento* deseja usar para receber pagamentos\\?\n\n",
            parse_mode="MarkdownV2",
            reply_markup=builder.as_markup()
        )
        await state.set_state(TokenState.selecting_gateway)
        
    async def livre_command(self, message: Message):
        """Comando para configurar o acesso público ao bot"""
        if message.from_user.id != self.owner_id:
            await message.reply(
                "⛔ *Este bot é privado*\\. Apenas o proprietário pode gerar PIX\\.\n\n"
                "Quer um bot para gerar pix\\? Cadastre\\-se\\: @GeradorPix\\_Bot",
                parse_mode="MarkdownV2"
            )
            return

        self.db.update_last_activity(self.bot_token)
        
        # Verifica o estado atual
        is_public = self.db.is_bot_public(self.bot_token)
        
        # Cria os botões com o estado atual
        builder = InlineKeyboardBuilder()
        builder.add(
            InlineKeyboardButton(
                text=f"Sim {('✅' if is_public else '')}",
                callback_data="livre_sim"
            )
        )
        builder.add(
            InlineKeyboardButton(
                text=f"Não {('✅' if not is_public else '')}",
                callback_data="livre_nao"
            )
        )
        builder.adjust(2)  # 2 botões por linha

        await message.reply(
            "🔓 *Deseja que seus clientes possam gerar PIX usando seu bot\\?*\n\n"
            "• *Sim* \\- Qualquer pessoa pode gerar PIX\n"
            "• *Não* \\- Apenas você pode gerar PIX",
            parse_mode="MarkdownV2",
            reply_markup=builder.as_markup()
        )

    async def handle_callback(self, callback: CallbackQuery, state: FSMContext):
        """Processa callbacks dos botões inline"""
        # Processa callbacks do comando /livre
        if callback.data.startswith("livre_"):
            # Verifica se é o dono
            if callback.from_user.id != self.owner_id:
                await callback.answer("Apenas o dono pode alterar essa configuração", show_alert=True)
                return

            is_public = callback.data == "livre_sim"
            self.db.update_bot_access(self.bot_token, is_public)
            
            # Atualiza os botões com o novo estado
            builder = InlineKeyboardBuilder()
            builder.add(
                InlineKeyboardButton(
                    text=f"Sim {('✅' if is_public else '')}",
                    callback_data="livre_sim"
                )
            )
            builder.add(
                InlineKeyboardButton(
                    text=f"Não {('✅' if not is_public else '')}",
                    callback_data="livre_nao"
                )
            )
            builder.adjust(2)

            await callback.message.edit_text(
                "🔓 *Deseja que seus clientes possam gerar PIX usando seu bot?*\n\n"
                "• *Sim* - Qualquer pessoa pode gerar PIX\n"
                "• *Não* - Apenas você pode gerar PIX",
                parse_mode="Markdown",
                reply_markup=builder.as_markup()
            )
            
            await callback.answer("✅ Configuração atualizada!" if is_public else "✅ Apenas você poderá gerar PIX!")
            return

        # Processa callback de cancelamento
        if callback.data == "cancel_gateway":
            await state.clear()
            welcome_message = (
                "*Bem-vindo ao Bot Gerador de PIX!* 🤖\n\n"
                "*/pix* 💠 - Gere códigos PIX copia e cola rapidamente para receber pagamentos\n\n"
                "*/gateway* ⚙️ - Configure seu gateway de pagamento e receba dinheiro"
            )
            
            # Adiciona o comando /livre apenas se for o dono
            if callback.from_user.id == self.owner_id:
                welcome_message += "\n\n*/livre* 🔓 - Configure quem pode gerar PIX no seu bot"
                
            await callback.message.edit_text(
                welcome_message,
                parse_mode="Markdown"
            )
        
        # Processa callbacks de seleção de gateway
        elif callback.data == "select_pushinpay":
            await self.update_gateway_type("pushinpay", callback.message, state)
        
        elif callback.data == "select_mp":
            await self.update_gateway_type("mercadopago", callback.message, state)
        
        elif callback.data == "gateway":
            # Volta para a seleção de gateway
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="💫 MERCADO PAGO", callback_data="select_mp"))
            builder.add(InlineKeyboardButton(text="💠 PUSHINPAY", callback_data="select_pushinpay"))
            builder.row(InlineKeyboardButton(text="❌ Cancelar", callback_data="cancel_gateway"))
            builder.adjust(1)

            await callback.message.edit_text(
                "🔄 *Selecione o Gateway de Pagamento:*\n\n"
                "Escolha qual gateway você deseja utilizar para receber pagamentos\\.",
                parse_mode="MarkdownV2",
                reply_markup=builder.as_markup()
            )
            await state.set_state(TokenState.selecting_gateway)
        
        # Responde ao callback para remover o "carregando"
        await callback.answer()

    @staticmethod
    def validate_gateway_token(token: str) -> bool:
        """Valida o formato do token da PushInPay"""
        parts = token.split("|")
        return (
            len(parts) == 2 and
            parts[0].isdigit() and
            len(parts[1]) >= 30
        )
        
    async def update_gateway_type(self, gateway_type: str, message: Message, state: FSMContext):
        """Atualiza o tipo de gateway e prepara para receber o token"""
        try:
            self.db.update_gateway_type(self.bot_token, gateway_type)
            
            if gateway_type == "pushinpay":
                builder = InlineKeyboardBuilder()
                builder.add(InlineKeyboardButton(text="❌ Cancelar", callback_data="cancel_gateway"))
                
                await message.edit_text(
                    "🔑 Configure a sua *conta da Pushinpay para receber pagamentos* enviando o Token abaixo:",
                    parse_mode="MarkdownV2",
                    reply_markup=builder.as_markup()
                )
                await state.set_state(TokenState.waiting_token)
            
            elif gateway_type == "mercadopago":
                # Gera URL de autorização do Mercado Pago
                auth_url = (
                    f"https://auth.mercadopago.com.br/authorization?client_id={MP_CLIENT_ID}"
                    f"&response_type=code&platform_id=mp&state={self.bot_token}"
                    f"&redirect_uri={MP_REDIRECT_URI}"
                )
                
                builder = InlineKeyboardBuilder()
                builder.add(InlineKeyboardButton(
                    text="🔗 Conectar Mercado Pago",
                    url=auth_url
                ))
                builder.add(InlineKeyboardButton(
                    text="❌ Cancelar",
                    callback_data="cancel_gateway"
                ))
                builder.adjust(1)
                
                await message.edit_text(
                    "🔑 *Como conectar sua conta do Mercado Pago?*\n\n"
                    "1\\. Clique no botão abaixo\n"
                    "2\\. Autorize o aplicativo\n"
                    "3\\. Aguarde a confirmação\n\n"
                    "> ✅ Após autorizar, você poderá gerar pagamentos\\!",
                    parse_mode="MarkdownV2",
                    reply_markup=builder.as_markup()
                )
            
        except Exception as e:
            logger.error(f"Erro ao atualizar tipo de gateway: {e}")
            builder = InlineKeyboardBuilder()
            builder.add(InlineKeyboardButton(text="🔙 Voltar", callback_data="gateway"))
            
            await message.edit_text(
                "❌ *Erro ao configurar gateway\\!*\n\n"
                "Ocorreu um erro ao atualizar suas configurações\\. "
                "Por favor, tente novamente\\.",
                parse_mode="MarkdownV2",
                reply_markup=builder.as_markup()
            )

    async def process_gateway_token(self, message: Message, state: FSMContext):
        """Processa o token do gateway enviado pelo usuário"""
        if not await self.check_permission(message):
            await state.clear()
            return

        self.db.update_last_activity(self.bot_token)

        token = message.text.strip()
        
        # Valida o formato do token
        if not self.validate_gateway_token(token):
            await message.reply(
                "⛔️ O Token é *inválido!* Certifique-se que enviou o token correto.\n",
                parse_mode="Markdown"
            )
            return

        try:
            # Atualiza o token do gateway no banco
            self.db.update_gateway_token(self.bot_token, token)

            await message.reply(
                "✅ Token configurado com sucesso!\n"
                "Agora você já pode gerar códigos PIX usando sua conta."
            )

            # Apaga a mensagem com o token por segurança
            await message.delete()

        except Exception as e:
            logger.error(f"Erro ao salvar token: {e}")
            await message.reply(
                "❌ Erro ao configurar o token.\n"
                "Por favor, tente novamente mais tarde."
            )

        finally:
            await state.clear()
            
    async def process_mp_token(self, message: Message, state: FSMContext):
        """Processa o token do Mercado Pago enviado pelo usuário"""
        if not await self.check_permission(message):
            await state.clear()
            return

        self.db.update_last_activity(self.bot_token)
        token = message.text.strip()

        try:
            # Testa se o token é válido criando uma instância do SDK
            sdk = mercadopago.SDK(token)
            result = sdk.payment().get_payment_methods()
            
            if result["status"] == 200:
                # Atualiza o token do gateway no banco
                self.db.update_gateway_token(self.bot_token, token)
                self.db.update_gateway_type(self.bot_token, "mercadopago")

                await message.reply(
                    "✅ Access Token do Mercado Pago configurado com sucesso!\n"
                    "Agora você já pode gerar códigos PIX usando sua conta."
                )

                # Apaga a mensagem com o token por segurança
                await message.delete()
            else:
                await message.reply(
                    "❌ Token inválido! Verifique se você enviou o Access Token correto do Mercado Pago."
                )

        except Exception as e:
            logger.error(f"Erro ao validar token MP: {e}")
            await message.reply(
                "❌ Erro ao validar o token. Verifique se é um Access Token válido do Mercado Pago."
            )

        finally:
            await state.clear()

    async def create_mp_pix_payment(self, amount: float, user_id: int) -> str | None:
        try:
            # Obtém as credenciais
            credentials = self.db.get_mp_credentials(self.bot_token)
            
            if not credentials:
                logger.error("Credenciais do Mercado Pago não encontradas")
                return None

            # Calcula o valor da taxa de serviço (3%)
            service_fee = amount * 0.03

            # Gera uma chave de idempotência única
            idempotency_key = str(uuid.uuid4())

            # Prepara os dados do pagamento com split
            payment_data = {
                "transaction_amount": float(f"{amount:.2f}"),
                "description": f"Pagamento PIX - User {user_id}",
                "payment_method_id": "pix",
                "payer": {
                    "email": f"user_{user_id}@test.com"
                },
                "application_fee": float(f"{service_fee:.2f}"),  # Taxa de 3%
                "notification_url": "https://seu-webhook-url.com/notification",  # Opcional
                "metadata": {
                    "user_id": user_id,
                    "bot_token": self.bot_token
                }
            }

            headers = {
                "Authorization": f"Bearer {credentials['access_token']}",
                "Content-Type": "application/json",
                "X-Idempotency-Key": idempotency_key
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.mercadopago.com/v1/payments",
                    json=payment_data,
                    headers=headers
                ) as response:
                    response_text = await response.text()
                    print(f"Status: {response.status}")
                    print(f"Resposta completa: {response_text}")

                    if response.status == 201:
                        result = await response.json()
                        return result["point_of_interaction"]["transaction_data"]["qr_code"]
                    else:
                        logger.error(f"Erro ao criar PIX MP: Status {response.status}, Resposta: {response_text}")
                        return None

        except Exception as e:
            logger.error(f"Erro ao criar PIX MP: {e}")
            return None

    async def pix_command(self, message: Message, state: FSMContext):
        if not await self.check_permission(message, "pix"):
            return

        self.db.update_last_activity(self.bot_token)
        
        # Verifica se é o dono do bot
        is_owner = message.from_user.id == self.owner_id
        
        # Verifica o tipo de gateway e credenciais
        gateway_token = self.db.get_gateway_token(self.bot_token)
        
        # Verifica se tem gateway configurado
        if not gateway_token:
            if is_owner:
                # Mensagem detalhada para o dono
                await message.reply(
                    "⚠️ *Gateway não configurado\\!*\n\n"
                    "Para gerar códigos PIX, você precisa primeiro configurar seu gateway de pagamento\\!\n\n"
                    "*1\\.* Use o comando /gateway\n"
                    "*2\\.* Escolha um Gateway\n"
                    "*3\\.* Comece a receber pagamentos\\!\n\n"
                    "> ✅ Após configurar, você poderá gerar códigos PIX instantaneamente\\.",
                    parse_mode="MarkdownV2"
                )
            else:
                # Mensagem simples para usuários
                await message.reply(
                    "⚠️ *Gateway não configurado!*\n\n"
                    "O bot ainda não está configurado para gerar PIX. Entre em contato com o proprietário do bot.\n",
                    parse_mode="Markdown"
                )
            return
                
        await message.reply(
            "📱 Digite o *valor do PIX* que você deseja gerar.\n\n"
            "- Ex: 100.00 para R$ 100,00",
            parse_mode="Markdown"
        )
        await state.set_state(TokenState.waiting_pix_value)

    async def process_pix_value(self, message: Message, state: FSMContext):
        """Processa o valor do PIX enviado pelo usuário"""
        if message.text.startswith('/'):
            await state.clear()
            return
                    
        if not await self.check_permission(message):
            await state.clear()
            return

        self.db.update_last_activity(self.bot_token)

        try:
            # Converte e valida o valor
            amount = float(message.text.replace(',', '.'))
            if amount < 1 or amount > 10000:
                await message.reply(
                    "*O valor foi ultrapassdo do limite\\.*\n\n"
                    "*Limite 𝗣𝘂𝘀𝗵𝗶𝗻𝗣𝗮𝘆:* Normalmente, contas PushInPay só podem gerar PIX de até R\$ 150,00\. Em alguns casos, podem gerar com valores maiores\.\n\n"
                    "*Limite 𝗠𝗲𝗿𝗰𝗮𝗱𝗼 𝗣𝗮𝗴𝗼:* Normalmente, contas Mercado Pago podem gerar PIX de até R\$ 10\.000,00\. Em alguns casos, podem gerar com valores maiores\.\n\n"
                    "> ⚠️ Alguns gateways podem ter limites diferentes por transação\.",
                    parse_mode="MarkdownV2"
                )
                return

            # Calcula a taxa de serviço (3%)
            service_fee = amount * 0.03
            
            # Verifica qual gateway está configurado
            gateway_type = self.db.get_gateway_type(self.bot_token)
            
            # Gera o código PIX de acordo com o gateway
            if gateway_type == "mercadopago":
                pix_code = await self.create_mp_pix_payment(amount, message.from_user.id)
            else:  # pushinpay
                pix_code = await self.create_pix_payment(amount, message.from_user.id)
            
            if pix_code:
                # Escapa caracteres especiais para Markdown
                def escape_markdown(text):
                    chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
                    for char in chars:
                        text = text.replace(char, f'\\{char}')
                    return text
                
                # Formata o valor e a taxa com escape adequado
                amount_str = escape_markdown(f"{amount:.2f}")
                fee_str = escape_markdown(f"{service_fee:.2f}")
                safe_pix = pix_code.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
                
                # Obtém o nome do gateway para exibição
                gateway_name = "Mercado Pago" if gateway_type == "mercadopago" else "PushInPay"
                
                # Mensagem com detalhes do pagamento
                await message.reply(
                    f"📱 *Código PIX gerado com sucesso\\!*\n\n"
                    f"💰 *Valor:* R$ {amount_str}\n"
                    f"📦 *Taxa de Serviço:* R$ {fee_str}\n\n"
                    f"```\n{safe_pix}\n```\n\n",
                    parse_mode="MarkdownV2"
                )
            else:
                # Mensagem de erro específica para cada gateway
                if gateway_type == "mercadopago":
                    error_msg = (
                        "❌ *Erro ao gerar o código PIX* - Possíveis motivos:\n\n"
                        "1. *Conexão Expirada:* Sua conexão com o Mercado Pago pode ter expirado\n\n"
                        "2. *Servidor Instável:* O servidor do Mercado Pago pode estar temporariamente indisponível\n\n"
                        "3. *Limite Excedido:* O valor pode ter excedido o limite da sua conta\n\n"
                        "💡 Tente usar o comando /gateway para reconectar sua conta."
                    )
                else:  # pushinpay
                    error_msg = (
                        "❌ *Erro ao gerar o código PIX* - Possíveis motivos:\n\n"
                        "1. *Limite de Valor:* Algumas contas PushinPay tem limite de até *R$ 150,00* por transação.\n\n"
                        "2. *Servidor Instável:* O servidor pode estar temporariamente lento.\n\n"
                        "3. *Token Incorreto:* Se você tiver cadastrado o token errado, o pix não será gerado\n\n"
                        "💡 *Dica:* Tente gerar novamente com um valor inferior ou verifique seu token."
                    )
                
                await message.reply(
                    error_msg,
                    parse_mode="Markdown"
                )

        except ValueError:
            await message.reply(
                "Por favor, digite um valor válido.\n"
                "Exemplo: 100.00 para R$ 100,00"
            )
        finally:
            await state.clear()

    async def create_pix_payment(self, amount: float, user_id: int) -> str | None:
        # Obtém o token configurado
        gateway_token = self.db.get_gateway_token(self.bot_token)
        if not gateway_token:
            logger.error("Token do gateway não configurado")
            return None

        # Converte para centavos
        amount_cents = int(amount * 100)

        # Calcula 1% do valor para o split
        split_value = int(amount_cents * 0.03)

        headers = {
            "Authorization": f"Bearer {gateway_token}",
            "Content-Type": "application/json"
        }
        
        # Configura o split de pagamento com o account_id
        split_rules = [
            {
                "value": split_value,  # 1% do valor em centavos
                "account_id": "9D60FF2D-4298-4AEF-89AB-F27AE6A9D68D"  # Seu account_id da PushInPay
            }
        ]
        
        data = {
            "value": amount_cents,
            "external_id": f"USER_{user_id}_{datetime.utcnow().timestamp()}",
            "split_rules": split_rules
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    PUSHINPAY_BASE_URL,
                    json=data,
                    headers=headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get("qr_code")
                    else:
                        logger.error(
                            f"Erro API PIX: {response.status} - {await response.text()}"
                        )
                        return None

        except Exception as e:
            logger.error(f"Erro ao criar PIX: {e}")
            return None

    async def start(self):
        try:
            print(f"Iniciando bot PIX...")
            await self.bot.delete_webhook(drop_pending_updates=True)
            me = await self.bot.get_me()
            print(f"Bot @{me.username} iniciado com sucesso!")
            
            # Inicia o limpador de bots inativos em background
            asyncio.create_task(self.clean_inactive_bots())
            
            # Inicia o bot
            await self.dp.start_polling(self.bot)
        except Exception as e:
            print(f"Erro ao iniciar bot: {e}")
            raise e
        finally:
            await self.bot.session.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Uso: python app.py BOT_TOKEN")
        sys.exit(1)
    
    bot_token = sys.argv[1]
    pix_bot = PixBot(bot_token)
    asyncio.run(pix_bot.start())