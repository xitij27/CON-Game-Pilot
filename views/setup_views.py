"""Multi-step match configuration wizard, sent as an ephemeral message."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Awaitable, Optional
import discord

from data.game_data import get_game_types, get_regions

# (offset_hours, label) — exactly 25 entries to fit a Discord select
_TZ_OPTIONS: list[tuple[int, str]] = [
    (-12, "UTC−12"),
    (-11, "UTC−11"),
    (-10, "UTC−10  (Hawaii)"),
    (-9,  "UTC−9   (Alaska)"),
    (-8,  "UTC−8   (US Pacific)"),
    (-7,  "UTC−7   (US Mountain)"),
    (-6,  "UTC−6   (US Central)"),
    (-5,  "UTC−5   (US Eastern)"),
    (-4,  "UTC−4   (US Eastern summer / Atlantic)"),
    (-3,  "UTC−3   (Brazil, Argentina)"),
    (-2,  "UTC−2"),
    (-1,  "UTC−1   (Azores)"),
    ( 0,  "UTC     (London winter, Reykjavik)"),
    ( 1,  "UTC+1   (Germany winter, France, Spain)"),
    ( 2,  "UTC+2   (Germany summer, Greece, Israel)"),
    ( 3,  "UTC+3   (Moscow, East Africa)"),
    ( 4,  "UTC+4   (UAE, Baku)"),
    ( 5,  "UTC+5   (Pakistan)"),
    ( 6,  "UTC+6   (Bangladesh)"),
    ( 7,  "UTC+7   (Bangkok, Jakarta)"),
    ( 8,  "UTC+8   (China, Singapore, Perth)"),
    ( 9,  "UTC+9   (Japan, South Korea)"),
    (10,  "UTC+10  (Sydney, Melbourne)"),
    (11,  "UTC+11  (Solomon Islands)"),
    (12,  "UTC+12  (New Zealand)"),
]


class SetupWizard(discord.ui.View):
    def __init__(self, leader: discord.Member, on_confirm: Callable[[discord.Interaction, "SetupWizard"], Awaitable[None]]):
        super().__init__(timeout=300)
        self.leader = leader
        self.game_type: Optional[str] = None
        self.region: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self._tz_offset: Optional[int] = None    # hours, e.g. +2 for Germany summer
        self._date: Optional[str] = None         # ISO date "YYYY-MM-DD" (local)
        self._hour: Optional[int] = None         # local hour
        self._minute: Optional[int] = None       # 0, 15, 30, or 45
        self._duration_minutes: int = 60
        self._at_time_step: bool = False         # disambiguates back-nav at tz vs time step
        self._on_confirm = on_confirm
        self._add_game_type_select()

    # ── item factories ────────────────────────────────────────────────────────

    def _add_game_type_select(self) -> None:
        self.clear_items()
        select = discord.ui.Select(
            placeholder="Choose a Game Type...",
            options=[discord.SelectOption(label=gt, value=gt) for gt in get_game_types()],
        )
        select.callback = self._on_game_type
        self.add_item(select)

    def _add_region_select(self) -> None:
        self.clear_items()
        regions = get_regions(self.game_type)
        if not regions:
            select = discord.ui.Select(
                placeholder="No regions available for this game type yet",
                options=[discord.SelectOption(label="—", value="none")],
                disabled=True,
            )
            self.add_item(select)
            self.add_item(self._back_button())
            return
        select = discord.ui.Select(
            placeholder="Choose a Region...",
            options=[discord.SelectOption(label=r, value=r) for r in regions],
        )
        select.callback = self._on_region
        self.add_item(select)
        self.add_item(self._back_button())

    def _add_timezone_select(self) -> None:
        self.clear_items()
        options = [
            discord.SelectOption(label=label, value=str(offset), default=(self._tz_offset == offset))
            for offset, label in _TZ_OPTIONS
        ]
        select = discord.ui.Select(placeholder="🌍  Your timezone...", options=options)
        select.callback = self._on_tz_offset
        self.add_item(select)
        self.add_item(self._back_button())

    def _add_time_selects(self) -> None:
        self.clear_items()
        now = datetime.now(timezone.utc)

        # Date: next 14 days
        date_options = []
        for i in range(14):
            d = now.date() + timedelta(days=i)
            val = d.isoformat()
            day_str = d.strftime("%a, %b %-d")
            if i == 0:
                label = f"Today — {day_str}"
            elif i == 1:
                label = f"Tomorrow — {day_str}"
            else:
                label = day_str
            date_options.append(discord.SelectOption(label=label, value=val, default=(self._date == val)))
        date_sel = discord.ui.Select(placeholder="📅  Start date...", options=date_options, row=0)
        date_sel.callback = self._on_date
        self.add_item(date_sel)

        # Hour: 00–23 (local)
        hour_options = [
            discord.SelectOption(label=f"{h:02d}:__", value=str(h), default=(self._hour == h))
            for h in range(24)
        ]
        hour_sel = discord.ui.Select(placeholder="🕐  Start hour...", options=hour_options, row=1)
        hour_sel.callback = self._on_hour
        self.add_item(hour_sel)

        # Minute: 15-min intervals
        minute_options = [
            discord.SelectOption(label=f"__{m:02d}", value=str(m), default=(self._minute == m))
            for m in (0, 15, 30, 45)
        ]
        minute_sel = discord.ui.Select(placeholder="⏱  Start minute...", options=minute_options, row=2)
        minute_sel.callback = self._on_minute
        self.add_item(minute_sel)

        # Confirm button — disabled until date, hour, and minute are chosen
        all_set = all(x is not None for x in (self._date, self._hour, self._minute))
        confirm_btn = discord.ui.Button(
            label="Confirm Time →",
            style=discord.ButtonStyle.primary,
            disabled=not all_set,
            row=3,
        )
        confirm_btn.callback = self._on_time_confirm
        self.add_item(confirm_btn)

        back_btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, emoji="◀️", row=3)
        back_btn.callback = self._on_back
        self.add_item(back_btn)

    def _add_confirm_buttons(self) -> None:
        self.clear_items()
        confirm = discord.ui.Button(label="Confirm & Create", style=discord.ButtonStyle.success, emoji="✅")
        confirm.callback = self._on_confirm_click
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
        cancel.callback = self._on_cancel
        self.add_item(confirm)
        self.add_item(cancel)
        self.add_item(self._back_button())

    def _back_button(self) -> discord.ui.Button:
        btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, emoji="◀️")
        btn.callback = self._on_back
        return btn

    # ── step embeds ───────────────────────────────────────────────────────────

    def _step_embed(self, title: str, description: str, step: int) -> discord.Embed:
        labels = ["Game Type", "Region", "Timezone", "Time", "Confirm"]
        progress = " → ".join(f"**[{l}]**" if i + 1 == step else l for i, l in enumerate(labels))
        embed = discord.Embed(title=f"🗺️ New Match — {title}", description=description, color=discord.Color.blue())
        if self.game_type:
            embed.add_field(name="Game Type", value=self.game_type, inline=True)
        if self.region:
            embed.add_field(name="Region", value=self.region, inline=True)
        if self._tz_offset is not None:
            sign = "+" if self._tz_offset >= 0 else ""
            embed.add_field(name="Timezone", value=f"UTC{sign}{self._tz_offset}", inline=True)
        if self.start_time:
            embed.add_field(name="Start", value=f"<t:{int(self.start_time.timestamp())}:F>", inline=True)
        if self.end_time:
            embed.add_field(name="End", value=f"<t:{int(self.end_time.timestamp())}:F>", inline=True)
        embed.set_footer(text=progress)
        return embed

    # ── callbacks ─────────────────────────────────────────────────────────────

    async def _on_game_type(self, interaction: discord.Interaction) -> None:
        self.game_type = interaction.data["values"][0]
        self.region = None
        regions = get_regions(self.game_type)
        desc = "Game type set. Now choose a region." if regions else f"⚠️ **{self.game_type}** has no region data yet."
        self._add_region_select()
        await interaction.response.edit_message(embed=self._step_embed("Region", desc, 2), view=self)

    async def _on_region(self, interaction: discord.Interaction) -> None:
        self.region = interaction.data["values"][0]
        self._tz_offset = None
        self._date = None
        self._hour = None
        self._minute = None

        self.start_time = None
        self.end_time = None
        self._at_time_step = False
        self._add_timezone_select()
        await interaction.response.edit_message(
            embed=self._step_embed("Timezone", "Select your local timezone so match times are shown correctly.", 3),
            view=self,
        )

    async def _on_tz_offset(self, interaction: discord.Interaction) -> None:
        self._tz_offset = int(interaction.data["values"][0])
        self._date = None
        self._hour = None
        self._minute = None

        self._at_time_step = True
        self._add_time_selects()
        sign = "+" if self._tz_offset >= 0 else ""
        await interaction.response.edit_message(
            embed=self._step_embed(
                "Time",
                f"All times are in **UTC{sign}{self._tz_offset}** (your local time).",
                4,
            ),
            view=self,
        )

    async def _on_date(self, interaction: discord.Interaction) -> None:
        self._date = interaction.data["values"][0]
        self._add_time_selects()
        sign = "+" if self._tz_offset >= 0 else ""
        await interaction.response.edit_message(
            embed=self._step_embed(
                "Time", f"All times are in **UTC{sign}{self._tz_offset}** (your local time).", 4
            ),
            view=self,
        )

    async def _on_hour(self, interaction: discord.Interaction) -> None:
        self._hour = int(interaction.data["values"][0])
        self._add_time_selects()
        sign = "+" if self._tz_offset >= 0 else ""
        await interaction.response.edit_message(
            embed=self._step_embed(
                "Time", f"All times are in **UTC{sign}{self._tz_offset}** (your local time).", 4
            ),
            view=self,
        )

    async def _on_minute(self, interaction: discord.Interaction) -> None:
        self._minute = int(interaction.data["values"][0])
        self._add_time_selects()
        sign = "+" if self._tz_offset >= 0 else ""
        await interaction.response.edit_message(
            embed=self._step_embed(
                "Time", f"All times are in **UTC{sign}{self._tz_offset}** (your local time).", 4
            ),
            view=self,
        )


    async def _on_time_confirm(self, interaction: discord.Interaction) -> None:
        # Build local datetime then shift to UTC
        local_start = datetime.fromisoformat(self._date).replace(
            hour=self._hour, minute=self._minute
        )
        start_utc = local_start - timedelta(hours=self._tz_offset)
        start_utc = start_utc.replace(tzinfo=timezone.utc)

        if start_utc <= datetime.now(timezone.utc):
            await interaction.response.send_message("Start time must be in the future.", ephemeral=True)
            return

        self.start_time = start_utc
        self.end_time = start_utc + timedelta(minutes=self._duration_minutes)
        self._at_time_step = False
        self._add_confirm_buttons()
        embed = self._step_embed(
            "Confirm",
            "Everything look right? Hit **Confirm & Create** to spin up the channel.",
            5,
        )
        embed.color = discord.Color.green()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_confirm_click(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self._on_confirm(interaction, self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="Setup cancelled.", color=discord.Color.red()),
            view=None,
        )

    async def _on_back(self, interaction: discord.Interaction) -> None:
        if self.start_time is not None:
            # Confirm → Time (preserve date/hour/minute/duration selections)
            self.start_time = None
            self.end_time = None
            self._at_time_step = True
            self._add_time_selects()
            sign = "+" if self._tz_offset >= 0 else ""
            await interaction.response.edit_message(
                embed=self._step_embed(
                    "Time", f"All times are in **UTC{sign}{self._tz_offset}** (your local time).", 4
                ),
                view=self,
            )
        elif self._at_time_step:
            # Time → Timezone (preserve tz selection so it shows as pre-selected)
            self._date = None
            self._hour = None
            self._minute = None
    
            self._at_time_step = False
            self._add_timezone_select()
            await interaction.response.edit_message(
                embed=self._step_embed("Timezone", "Select your local timezone.", 3), view=self
            )
        elif self.region is not None:
            # Timezone → Region
            self.region = None
            self._tz_offset = None
            self._add_region_select()
            await interaction.response.edit_message(
                embed=self._step_embed("Region", "Choose a region.", 2), view=self
            )
        else:
            # Region → Game Type
            self.game_type = None
            self._add_game_type_select()
            await interaction.response.edit_message(
                embed=self._step_embed("Game Type", "Choose a game type to begin.", 1), view=self
            )

    async def on_timeout(self) -> None:
        self.stop()
