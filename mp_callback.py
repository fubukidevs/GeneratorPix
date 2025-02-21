# mp_callback.py
from aiohttp import web
import aiohttp
import logging
import sqlite3
from aiogram import Bot

from config import (
    MP_CLIENT_ID,
    MP_CLIENT_SECRET,
    MP_REDIRECT_URI,
    ADMIN_BOT_TOKEN
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.db_file = "bots.db"

    def save_mp_credentials(self, bot_token: str, access_token: str, refresh_token: str, user_id: str):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE bots 
            SET gateway_token = ?,
                mp_refresh_token = ?,
                mp_user_id = ?,
                gateway_type = 'mercadopago'
            WHERE token = ?
        ''', (access_token, refresh_token, user_id, bot_token))
        
        conn.commit()
        conn.close()

    def get_user_id_by_bot_token(self, bot_token: str):
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id FROM bots WHERE token = ?', (bot_token,))
        result = cursor.fetchone()
        
        conn.close()
        return result[0] if result else None

async def exchange_code_for_token(code: str):
    """Troca o código de autorização por tokens de acesso"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            'https://api.mercadopago.com/oauth/token',
            json={
                'client_secret': MP_CLIENT_SECRET,
                'client_id': MP_CLIENT_ID,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': MP_REDIRECT_URI
            }
        ) as response:
            return await response.json()

success_html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Conexão Realizada</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: #f0f2f5;
        }
        .container {
            text-align: center;
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            margin: 20px;
            max-width: 90%;
        }
        .success-icon {
            font-size: 80px;
            margin-bottom: 20px;
        }
        h1 {
            color: #32CD32;
            font-size: 28px;
            margin-bottom: 20px;
        }
        p {
            font-size: 18px;
            color: #666;
            margin-bottom: 30px;
            line-height: 1.6;
        }
        .close-text {
            font-size: 16px;
            color: #888;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="success-icon">✅</div>
        <h1>Conta Conectada com Sucesso!</h1>
        <p>Sua conta do Mercado Pago foi vinculada ao bot.</p>
        <p>Volte para o Telegram e comece a gerar pagamentos PIX!</p>
        <p class="close-text">Você já pode fechar esta janela.</p>
    </div>
</body>
</html>
"""

async def handle_mp_callback(request):
    """Handler para o callback do Mercado Pago"""
    try:
        # Obtém os parâmetros da URL
        code = request.query.get('code')
        state = request.query.get('state')  # state contém o bot_token
        
        if not code or not state:
            return web.Response(text="Parâmetros inválidos", status=400)

        # Troca o código por tokens
        token_data = await exchange_code_for_token(code)
        
        if 'access_token' not in token_data:
            logger.error(f"Erro ao obter token: {token_data}")
            return web.Response(text="Erro ao obter token", status=400)

        # Importante: Use o bot_token (state) específico ao salvar as credenciais
        db = Database()
        
        # Log para debug
        logger.info(f"Salvando credenciais MP para bot token: {state}")
        logger.info(f"Access Token: {token_data['access_token'][:10]}...")
        
        # Salva as credenciais no banco
        db.save_mp_credentials(
            bot_token=state,  # Este é o token específico do bot
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            user_id=str(token_data['user_id'])
        )

        # Notifica o usuário usando o bot correto
        user_id = db.get_user_id_by_bot_token(state)
        if user_id:
            async with Bot(token=state) as user_bot:  # Usa context manager para fechar a sessão
                await user_bot.send_message(
                    user_id,
                    "✅ *Conta Mercado Pago conectada com sucesso!*\n\n"
                    "Você já pode gerar pagamentos PIX.",
                    parse_mode="Markdown"
                )

        return web.Response(
            text=success_html,
            content_type='text/html'
        )

    except Exception as e:
        logger.error(f"Erro no callback: {e}")
        return web.Response(text="Erro interno", status=500)

# Cria a aplicação web
app = web.Application()
app.router.add_get('/mp/callback', handle_mp_callback)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8080)