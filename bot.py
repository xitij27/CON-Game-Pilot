import discord

import config
import database as db
from views.register_view import RegisterMatchView, RegistrationCardView


class StrikeBot(discord.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._initialized = False  # guard against on_ready firing multiple times

    async def on_ready(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        print(f"[strikebot] Logged in as {self.user}  (id: {self.user.id})", flush=True)

        print("[strikebot] Initialising DB...", flush=True)
        await db.init_db()

        print("[strikebot] Loading cogs...", flush=True)
        try:
            self.load_extension("cogs.match")
            print("[strikebot] Cog loaded OK.", flush=True)
        except Exception as e:
            import traceback
            print(f"[strikebot] ERROR loading cog: {e}", flush=True)
            traceback.print_exc()
            return

        print("[strikebot] Restoring persistent views...", flush=True)
        await self._restore_views()

        print(f"[strikebot] Pending commands: {len(self.pending_application_commands)}", flush=True)
        print("[strikebot] Syncing commands...", flush=True)
        try:
            await self.sync_commands()
            print("[strikebot] Commands synced.", flush=True)
        except Exception as e:
            print(f"[strikebot] ERROR syncing commands: {e}", flush=True)

    async def _restore_views(self) -> None:
        open_matches = await db.get_open_matches()
        for match in open_matches:
            self.add_view(RegisterMatchView(match["channel_id"]))

        active_regs = await db.get_all_active_registrations()
        for reg in active_regs:
            self.add_view(RegistrationCardView(reg["id"]))

        print(
            f"[strikebot] Restored {len(open_matches)} match view(s) "
            f"and {len(active_regs)} registration card view(s).",
            flush=True,
        )


def main() -> None:
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN is not set. Copy .env.example → .env and fill it in.")
    if not config.GUILD_ID:
        raise SystemExit("SERVER_ID is not set. Add your server's ID to .env.")

    intents = discord.Intents.default()
    intents.members = True

    bot = StrikeBot(intents=intents, debug_guilds=[config.GUILD_ID])
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
