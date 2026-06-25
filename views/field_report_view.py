"""Field report wizard: per-player score modals → formatted report posted to channel."""
from __future__ import annotations

from typing import Awaitable, Callable, Optional
import discord
import config

# ── Score schemas ──────────────────────────────────────────────────────────────

_LEADER_SCHEMA: list[tuple[str, int]] = [
    ("Strategy", 3),
    ("Strength", 3),
    ("Activeness", 3),
    ("Communication", 3),
    ("Leadership", 2),
    ("Difference Maker", 1),
]
_DEFAULT_SCHEMA: list[tuple[str, int]] = [
    ("Strategy", 3),
    ("Strength", 3),
    ("Activeness", 3),
    ("Communication", 3),
    ("Followed Orders", 2),
    ("Difference Maker", 1),
]


def _schema_for(squad_role: str) -> list[tuple[str, int]]:
    return _LEADER_SCHEMA if squad_role == "Leader" else _DEFAULT_SCHEMA


def _max_score(schema: list[tuple[str, int]]) -> int:
    return sum(m for _, m in schema)


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


def _get_rank_mention(member: discord.Member | None) -> str | None:
    if not member:
        return None
    rank_roles = [r for r in member.roles if r.name in config.ALLOWED_RANKS]
    if not rank_roles:
        return None
    rank_roles.sort(key=lambda r: config.ALLOWED_RANKS.index(r.name), reverse=True)
    return rank_roles[0].mention


# ── Score parsing ──────────────────────────────────────────────────────────────

def _parse_scores(raw_a: str, raw_b: str, schema: list[tuple[str, int]]) -> list[float] | str:
    """Return list of floats on success, or an error string on failure."""
    combined = [p.strip() for p in (raw_a + "," + raw_b).split(",") if p.strip()]
    if len(combined) != len(schema):
        return f"Expected {len(schema)} numbers total, got {len(combined)}."
    result: list[float] = []
    for part, (name, max_val) in zip(combined, schema):
        try:
            v = float(part)
        except ValueError:
            return f"'{part}' is not a number ({name})."
        if v < 0 or v > max_val:
            return f"{name} must be 0–{max_val}, got {v}."
        result.append(v)
    return result


# ── Report formatter ───────────────────────────────────────────────────────────

def _build_report(
    players: list[dict],
    members: dict[int, discord.Member | None],
    overall_comments: str,
) -> str:
    lines = ["__**📝 Field Report**__", ""]
    for p in players:
        schema = _schema_for(p["squad_role"])
        scores = p["scores"]
        total = sum(scores)
        max_total = _max_score(schema)

        lines.append(f"ID: <@{p['user_id']}>")
        rank = _get_rank_mention(members.get(p["user_id"]))
        if rank:
            lines.append(f"Rank: {rank}")
        lines += [
            f"Nation: {p['primary_country']}",
            f"Squad Role: {p['squad_role']}",
            f"Military Role: {p['military_role'] or '—'}",
        ]
        for (name, max_val), score in zip(schema, scores):
            lines.append(f"{name}: {_fmt(score)} /{max_val}")
        lines.append(f"Score : {_fmt(total)} /{max_total}")
        if p.get("comments"):
            lines.append(f"Comments: {p['comments']}")
        lines += ["", "=================================", ""]

    lines.append(f"Overall Comments: {overall_comments}")
    return "\n".join(lines)


def _chunk(text: str, max_len: int = 1900) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        segment = line + "\n"
        if len(current) + len(segment) > max_len:
            if current:
                chunks.append(current.rstrip())
            current = segment
        else:
            current += segment
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


# ── Modals ─────────────────────────────────────────────────────────────────────

class _PlayerScoreModal(discord.ui.Modal):
    def __init__(self, wizard: FieldReportWizard, reg: dict, member: discord.Member | None):
        squad_role = reg["squad_role"]
        name_str = (member.display_name if member else str(reg["user_id"]))[:25]
        super().__init__(title=f"Score: {name_str}")

        self._wizard = wizard
        self._reg = reg
        self._schema = _schema_for(squad_role)

        fifth = (
            "Leadership(2) · DM(1)"
            if squad_role == "Leader"
            else "Fol.Orders(2) · DM(1)"
        )
        self.scores_a = discord.ui.InputText(
            label="Strategy · Strength · Activeness  (0-3 each)",
            placeholder="3,3,3",
            style=discord.InputTextStyle.short,
            max_length=12,
        )
        self.scores_b = discord.ui.InputText(
            label=f"Communication(3) · {fifth}",
            placeholder="3,2,1",
            style=discord.InputTextStyle.short,
            max_length=12,
        )
        self.comments_input = discord.ui.InputText(
            label="Comments",
            placeholder="Brief evaluation of this player's performance.",
            style=discord.InputTextStyle.long,
            required=False,
            max_length=500,
        )
        self.add_item(self.scores_a)
        self.add_item(self.scores_b)
        self.add_item(self.comments_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        result = _parse_scores(self.scores_a.value, self.scores_b.value, self._schema)
        if isinstance(result, str):
            await interaction.response.send_message(
                f"❌ {result}\n"
                "Expected format — first field: `3,3,3`  ·  second field: `3,2,1`",
                ephemeral=True,
            )
            return

        uid = self._reg["user_id"]
        self._wizard.player_data[uid] = {
            **self._reg,
            "scores": result,
            "comments": self.comments_input.value.strip(),
        }
        await interaction.response.defer()
        await self._wizard._original_interaction.edit_original_response(
            embed=self._wizard.build_embed(), view=self._wizard
        )


class _OverallCommentsModal(discord.ui.Modal):
    def __init__(self, wizard: FieldReportWizard):
        super().__init__(title="Overall Comments")
        self._wizard = wizard
        self.comments_input = discord.ui.InputText(
            label="Overall Comments",
            placeholder="Summarize the match outcome, team performance, etc.",
            style=discord.InputTextStyle.long,
            max_length=1000,
        )
        self.add_item(self.comments_input)

    async def callback(self, interaction: discord.Interaction) -> None:
        self._wizard.overall_comments = self.comments_input.value.strip()
        await interaction.response.defer()
        await self._wizard._original_interaction.edit_original_response(
            embed=self._wizard.build_embed(), view=self._wizard
        )


# ── Wizard view ────────────────────────────────────────────────────────────────

class FieldReportWizard(discord.ui.View):
    def __init__(
        self,
        match: dict,
        regs: list[dict],
        members: dict[int, discord.Member | None],
        original_interaction: discord.Interaction,
        on_complete: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
    ):
        super().__init__(timeout=600)
        self.match = match
        self.regs = regs
        self.members = members
        self._original_interaction = original_interaction
        self._on_complete = on_complete
        self.player_data: dict[int, dict] = {}
        self.overall_comments: str = ""

        for reg in regs:
            uid = reg["user_id"]
            member = members.get(uid)
            label = (member.display_name if member else str(uid))[:20]
            btn = discord.ui.Button(
                label=f"⬜ {label}",
                style=discord.ButtonStyle.primary,
                custom_id=f"fr_player_{uid}",
            )
            btn.callback = self._make_player_callback(reg, member)
            self.add_item(btn)

        self._overall_btn = discord.ui.Button(
            label="⬜ Overall Comments",
            style=discord.ButtonStyle.secondary,
        )
        self._overall_btn.callback = self._overall_callback
        self.add_item(self._overall_btn)

        self._post_btn = discord.ui.Button(
            label="📋 Post Report",
            style=discord.ButtonStyle.success,
            disabled=True,
        )
        self._post_btn.callback = self._post_callback
        self.add_item(self._post_btn)

    def _make_player_callback(self, reg: dict, member: discord.Member | None):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(_PlayerScoreModal(self, reg, member))
        return callback

    async def _overall_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_OverallCommentsModal(self))

    async def _post_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ordered = [
            self.player_data[r["user_id"]]
            for r in self.regs
            if r["user_id"] in self.player_data
        ]
        report = _build_report(ordered, self.members, self.overall_comments)
        for chunk in _chunk(report):
            await interaction.channel.send(chunk)

        archive_note = "\nArchiving channel…" if self._on_complete else ""
        await self._original_interaction.edit_original_response(
            embed=discord.Embed(
                title="✅ Field report posted!" + archive_note,
                color=discord.Color.green(),
            ),
            view=None,
        )
        self.stop()

        if self._on_complete:
            await self._on_complete(interaction)

    def _update_buttons(self) -> None:
        scored = set(self.player_data)
        all_scored = bool(self.regs) and all(r["user_id"] in scored for r in self.regs)
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            cid = getattr(item, "custom_id", None)
            if cid and cid.startswith("fr_player_"):
                uid = int(cid.removeprefix("fr_player_"))
                m = self.members.get(uid)
                label = (m.display_name if m else str(uid))[:20]
                item.label = f"{'✅' if uid in scored else '⬜'} {label}"
            elif item is self._overall_btn:
                item.label = f"{'✅' if self.overall_comments else '⬜'} Overall Comments"
            elif item is self._post_btn:
                item.disabled = not (all_scored and bool(self.overall_comments))

    def build_embed(self) -> discord.Embed:
        self._update_buttons()
        scored = set(self.player_data)
        done = len(scored)
        total = len(self.regs)
        extra = " · Overall ✅" if self.overall_comments else ""
        embed = discord.Embed(
            title="📝 Field Report Wizard",
            description=(
                f"Click each player to score them, then add overall comments.\n"
                f"**{done}/{total}** players scored{extra}\n"
                f"-# DM = Difference Maker"
            ),
            color=discord.Color.blue(),
        )
        for reg in self.regs:
            uid = reg["user_id"]
            m = self.members.get(uid)
            name = m.display_name if m else f"User {uid}"
            if uid in scored:
                pd = self.player_data[uid]
                schema = _schema_for(reg["squad_role"])
                total_score = sum(pd["scores"])
                max_score = _max_score(schema)
                embed.add_field(
                    name=f"✅ {name} — {reg['primary_country']}",
                    value=f"Score: **{_fmt(total_score)}/{max_score}**",
                    inline=True,
                )
            else:
                embed.add_field(
                    name=f"⬜ {name}",
                    value=f"{reg['squad_role']} · {reg['primary_country']}",
                    inline=True,
                )
        return embed
