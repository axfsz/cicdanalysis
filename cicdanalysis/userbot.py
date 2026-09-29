"""Read release-group messages with a Telegram user account (MTProto).

Telegram never delivers one bot's group messages to another bot, and each
release group is served by its own release bot (@ugopsbot, @uguatdeploybot,
@ugprodopsbot). A member user account sees every message, so it can feed the
same parsers without any change to the release bots.

Optional: needs ``telethon`` plus TELEGRAM_USER_API_ID / TELEGRAM_USER_API_HASH
and a one-time ``python3 -m cicdanalysis telegram-login`` on the server.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


def to_update(chat_id: int, message, sender_username: str = "") -> dict:
    """Shape a Telethon message like a Bot API update so the existing parsers apply unchanged."""
    return {"update_id": 0, "message": {
        "message_id": message.id, "date": int(message.date.timestamp()) if message.date else None,
        "chat": {"id": int(chat_id)}, "text": message.message or "",
        "from": {"username": sender_username},
        # Message ids of basic groups (no -100 prefix) are per account, so the
        # bot cannot reply to this id; supergroup ids are shared.
        "reply_unsafe": not str(chat_id).startswith("-100")}}


class UserAccountListener:
    def __init__(self, app):
        self.app, self.config = app, app.config
        self.trusted = {x.strip().lstrip("@").lower() for x in self.config.telegram_user_trusted_senders.split(",") if x.strip()}
        # Set while the live listener is connected. The session file is a
        # SQLite database that only one process can hold, so history replay
        # must run on this connection instead of opening a second client.
        self.client = None
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def configured(self) -> bool:
        return bool(self.config.telegram_user_api_id and self.config.telegram_user_api_hash)

    def _client(self):
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("Telegram user-account mode requires telethon: pip install telethon") from exc
        return TelegramClient(self.config.telegram_user_session, int(self.config.telegram_user_api_id),
                              self.config.telegram_user_api_hash)

    def _chat_ids(self) -> list[int]:
        return [int(chat) for chat in self.config.chat_environments()]

    async def _sender(self, message) -> str:
        try:
            sender = await message.get_sender()
        except Exception:
            return ""
        return (getattr(sender, "username", "") or "").lower()

    async def _handle(self, chat_id: int, message) -> dict | None:
        username = await self._sender(message)
        if self.trusted and username not in self.trusted:
            return None
        try:
            return self.app.handle_update(to_update(chat_id, message, username))
        except Exception:
            log.exception("cannot handle release message %s:%s", chat_id, message.id)
            return None

    # -- one-time interactive login ------------------------------------------------
    def login(self) -> None:
        async def run():
            client = self._client()
            await client.start()  # prompts for phone, login code and 2FA password in this terminal
            me = await client.get_me()
            print(f"logged in as {me.first_name or ''} (@{me.username or '-'}); session saved to {self.config.telegram_user_session}")
            await client.get_dialogs()
            for chat_id, env in self.config.chat_environments().items():
                try:
                    entity = await client.get_entity(int(chat_id))
                    print(f"  {env.upper():5} {chat_id}  OK  {getattr(entity, 'title', '')}")
                except Exception as exc:
                    print(f"  {env.upper():5} {chat_id}  NOT ACCESSIBLE ({exc.__class__.__name__}); join this group with the account")
            await client.disconnect()
        asyncio.run(run())

    # -- history replay ---------------------------------------------------------------
    async def _replay(self, client, days: int) -> dict:
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        stats = {"days": days, "messages": 0, "handled": 0}
        for chat_id in self._chat_ids():
            async for message in client.iter_messages(chat_id, offset_date=since, reverse=True):
                stats["messages"] += 1
                if message.message and await self._handle(chat_id, message) is not None:
                    stats["handled"] += 1
        return stats

    @property
    def running(self) -> bool:
        return self.client is not None and self.loop is not None and self.loop.is_running()

    def backfill(self, days: int, timeout: float = 900) -> dict:
        """Replay group history: on the live connection when running, else with a short-lived client."""
        if self.running:
            future = asyncio.run_coroutine_threadsafe(self._replay(self.client, days), self.loop)
            return future.result(timeout)

        async def run():
            client = self._client()
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise RuntimeError("not logged in; run: python3 -m cicdanalysis telegram-login")
                await client.get_dialogs()
                return await self._replay(client, days)
            finally:
                await client.disconnect()
        return asyncio.run(run())

    # -- live listener ----------------------------------------------------------------
    async def _listen(self) -> None:
        from telethon import events
        client = self._client()
        await client.connect()
        if not await client.is_user_authorized():
            log.error("Telegram user account is not logged in; run: python3 -m cicdanalysis telegram-login")
            await client.disconnect()
            return
        await client.get_dialogs()  # cache group entities so chat filters resolve
        chats = self._chat_ids()

        @client.on(events.NewMessage(chats=chats))
        @client.on(events.MessageEdited(chats=chats))
        async def on_message(event):
            await self._handle(event.chat_id, event.message)

        log.info("Telegram user-account listener started for %s groups", len(chats))
        self.client, self.loop = client, asyncio.get_running_loop()
        try:
            # Pick up messages posted while the service was down; replay is idempotent.
            if self.config.telegram_user_backfill_days > 0:
                try:
                    log.info("Telegram startup backfill: %s", await self._replay(client, self.config.telegram_user_backfill_days))
                except Exception:
                    log.exception("Telegram startup backfill failed")
            await client.run_until_disconnected()
        finally:
            self.client = self.loop = None

    def run_forever(self, stop) -> None:
        while not stop.is_set():
            try:
                asyncio.run(self._listen())
            except Exception:
                log.exception("Telegram user-account listener stopped; retrying")
            stop.wait(60)
