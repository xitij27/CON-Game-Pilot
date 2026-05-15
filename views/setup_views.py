"""Multi-step match configuration wizard, sent as an ephemeral message."""
from __future__ import annotations

from typing import Callable, Awaitable, Optional
import discord

from data.game_data import get_game_types, get_regions


class SetupWizard(discord.ui.View):
    def __init__(self, leader: discord.Member, on_confirm: Callable[[discord.Interaction, "SetupWizard"], Awaitable[None]]):
        super().__init__(timeout=300)
        self.leader = leader
        self.game_type: Optional[str] = None
        self.region: Optional[str] = None
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
        labels = ["Game Type", "Region", "Confirm"]
        progress = " → ".join(f"**[{l}]**" if i + 1 == step else l for i, l in enumerate(labels))
        embed = discord.Embed(title=f"🗺️ New Match — {title}", description=description, color=discord.Color.blue())
        if self.game_type:
            embed.add_field(name="Game Type", value=self.game_type, inline=True)
        if self.region:
            embed.add_field(name="Region", value=self.region, inline=True)
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
        self._add_confirm_buttons()
        embed = self._step_embed("Confirm", "Everything look right? Hit **Confirm & Create** to spin up the channel.", 3)
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
        if self.region is not None:
            self.region = None
            self._add_region_select()
            await interaction.response.edit_message(
                embed=self._step_embed("Region", "Choose a region.", 2), view=self
            )
        else:
            self.game_type = None
            self._add_game_type_select()
            await interaction.response.edit_message(
                embed=self._step_embed("Game Type", "Choose a game type to begin.", 1), view=self
            )

    async def on_timeout(self) -> None:
        self.stop()
