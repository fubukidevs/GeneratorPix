# start_system.py
import asyncio
import subprocess
import sys
import os
import signal
import sqlite3
from typing import List
from aiohttp import web
import logging
from mp_callback import handle_mp_callback

# Configuração do logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.db_file = "bots.db"
        self.init_db()

    def init_db(self):
        """Inicializa o banco de dados com as tabelas necessárias"""
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
        
        # Cria a tabela de processos se não existir
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_processes (
                token TEXT PRIMARY KEY,
                pid INTEGER NOT NULL
            )
        ''')
        
        conn.commit()
        conn.close()

    def get_active_bots(self):
        """Obtém todos os bots ativos do banco de dados"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM bots WHERE is_active = 1')
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

    def clear_processes(self):
        """Limpa a tabela de processos"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM bot_processes')
        
        conn.commit()
        conn.close()

class SystemManager:
    def __init__(self):
        self.processes: List[subprocess.Popen] = []
        self.db = Database()
        self.web_app = None
        self.runner = None

    def start_cadastro_bot(self):
        """Inicia o bot de cadastro"""
        try:
            process = subprocess.Popen([sys.executable, "cadastro.py"])
            self.processes.append(process)
            logger.info("✅ Bot de cadastro iniciado")
        except Exception as e:
            logger.error(f"❌ Erro ao iniciar bot de cadastro: {e}")
            raise e

    def start_pix_bots(self):
        """Inicia todos os bots PIX cadastrados"""
        try:
            bots = self.db.get_active_bots()
            for bot in bots:
                if bot.get("is_active"):
                    process = subprocess.Popen(
                        [sys.executable, "app.py", bot["token"]]
                    )
                    self.processes.append(process)
                    logger.info(f"✅ Bot PIX iniciado: @{bot['bot_username']}")
        except Exception as e:
            logger.error(f"❌ Erro ao iniciar bots PIX: {e}")
            raise e

    async def setup_callback_server(self):
        """Configura o servidor de callback do Mercado Pago"""
        try:
            self.web_app = web.Application()
            self.web_app.router.add_get('/mp/callback', handle_mp_callback)
            
            self.runner = web.AppRunner(self.web_app)
            await self.runner.setup()
            
            site = web.TCPSite(self.runner, 'localhost', 8080)
            await site.start()
            
            logger.info("✅ Servidor de callback do Mercado Pago iniciado na porta 8080")
        except Exception as e:
            logger.error(f"❌ Erro ao iniciar servidor de callback: {e}")
            raise e

    async def cleanup(self):
        """Limpa recursos e fecha conexões"""
        logger.info("\n🧹 Iniciando limpeza do sistema...")
        
        # Para todos os processos
        for process in self.processes:
            try:
                process.terminate()
                process.wait(timeout=5)
            except:
                process.kill()
        
        # Fecha o servidor web se estiver rodando
        if self.runner:
            await self.runner.cleanup()
        
        logger.info("✅ Limpeza concluída")

    def signal_handler(self, signum, frame):
        """Handler para sinais de interrupção"""
        logger.info("\n🛑 Sinal de parada recebido. Iniciando desligamento...")
        asyncio.create_task(self.cleanup())
        sys.exit(0)

    async def run(self):
        """Inicia todo o sistema"""
        try:
            logger.info("\n🚀 Iniciando sistema de bots PIX...")
            
            # Limpa registros de processos antigos
            self.db.clear_processes()
            
            # Registra handlers para sinais
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
            
            # Inicia componentes
            self.start_cadastro_bot()
            self.start_pix_bots()
            await self.setup_callback_server()
            
            logger.info("\n✨ Sistema iniciado com sucesso!")
            logger.info("📝 Pressione Ctrl+C para parar todos os serviços")
            
            # Mantém o sistema rodando
            while True:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ Erro fatal ao iniciar sistema: {e}")
            await self.cleanup()
            sys.exit(1)

if __name__ == "__main__":
    try:
        manager = SystemManager()
        asyncio.run(manager.run())
    except KeyboardInterrupt:
        logger.info("\n👋 Sistema encerrado pelo usuário")
    except Exception as e:
        logger.error(f"❌ Erro não tratado: {e}")
        sys.exit(1)