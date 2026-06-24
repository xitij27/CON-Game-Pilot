import discord

import config
import database as db
from views.register_view import MatchChannelView, RegistrationCardView
from views.hub_view import MatchHubControlView


class CONGamePilot(discord.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._initialized = False  # guard against on_ready firing multiple times

    async def on_ready(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        print(f"[con-game-pilot] Logged in as {self.user}  (id: {self.user.id})", flush=True)

        print("[con-game-pilot] Initialising DB...", flush=True)
        await db.init_db()

        print("[con-game-pilot] Loading cogs...", flush=True)
        try:
            self.load_extension("cogs.match")
            self.load_extension("cogs.hub")
            print("[con-game-pilot] Cogs loaded OK.", flush=True)
        except Exception as e:
            import traceback
            print(f"[con-game-pilot] ERROR loading cog: {e}", flush=True)
            traceback.print_exc()
            return

        print("[con-game-pilot] Restoring persistent views...", flush=True)
        await self._restore_views()

        print("[con-game-pilot] Setting up match-hub...", flush=True)
        guild = self.get_guild(config.GUILD_ID)
        if guild:
            hub_cog = self.cogs.get("HubCog")
            if hub_cog:
                await hub_cog.setup_hub(guild)
        else:
            print("[con-game-pilot] WARNING: guild not found — hub setup skipped.", flush=True)

        print(f"[con-game-pilot] Pending commands: {len(self.pending_application_commands)}", flush=True)
        print("[con-game-pilot] Syncing commands...", flush=True)
        try:
            await self.sync_commands()
            print("[con-game-pilot] Commands synced.", flush=True)
        except Exception as e:
            print(f"[con-game-pilot] ERROR syncing commands: {e}", flush=True)

    async def _restore_views(self) -> None:
        # Register views for every non-cancelled match so that clicking the
        # Register button on locked/started/ended channels returns a proper
        # "game has ended" message instead of Discord's "interaction failed".
        all_matches = await db.get_non_cancelled_matches()
        for match in all_matches:
            if match["status"] not in ("won", "lost", "cancelled"):
                self.add_view(MatchChannelView(match["channel_id"], match["status"]))

        active_regs = await db.get_all_active_registrations()
        for reg in active_regs:
            self.add_view(RegistrationCardView(reg["id"]))

        # Hub control panel (Create Match button) — one global view
        self.add_view(MatchHubControlView())

        open_count = sum(1 for m in all_matches if m["status"] == "open")
        print(
            f"[con-game-pilot] Restored {len(all_matches)} match view(s) "
            f"({open_count} open), {len(active_regs)} registration card view(s), "
            f"and hub control panel.",
            flush=True,
        )


def main() -> None:
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN is not set. Copy .env.example → .env and fill it in.")
    if not config.GUILD_ID:
        raise SystemExit("SERVER_ID is not set. Add your server's ID to .env.")

    intents = discord.Intents.default()
    intents.members = True

    bot = CONGamePilot(intents=intents, debug_guilds=[config.GUILD_ID])
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
