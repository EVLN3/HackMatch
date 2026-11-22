"""Discord client: quiz UI, slash commands, and deadline-triggered matching.

Everything is scoped per server (guild), since the same bot can run several
hackathons at once — one per server — with entirely separate signups, teams,
and matching. See storage.py for the data-layer side of this."""
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import tasks

from hack_match import storage
from hack_match.matching import compute_best_global_team_matches, team_compatibility_score
from hack_match.quiz import QUESTIONS, Profile, Question

MATCH_EMOJI = "\N{HANDSHAKE}"
TOP_CANDIDATES_PER_USER = 5
MAX_TEAM_SIZE = 4


def _effective_deadline(conn: object, guild_id: int, default_deadline: datetime) -> datetime:
    """A server's own /set_deadline override, if any, else the .env default
    shared by every server this bot is in."""
    override = storage.get_guild_deadline(conn, guild_id)
    if override is None:
        return default_deadline
    deadline = datetime.fromisoformat(override)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline


def _status_color_and_text(current_size: int) -> tuple[discord.Color, str]:
    """Mudae-style status by team size: green (1) -> yellow (2) -> orange (3)
    -> red (full, MAX_TEAM_SIZE)."""
    still_missing = MAX_TEAM_SIZE - current_size
    if still_missing <= 0:
        return discord.Color.red(), "\U0001f534 Full — not accepting applications."
    if current_size <= 1:
        color, emoji = discord.Color.green(), "\U0001f7e2"
    elif current_size == 2:
        color, emoji = discord.Color.gold(), "\U0001f7e1"
    else:
        color, emoji = discord.Color.orange(), "\U0001f7e0"
    plural = "" if still_missing == 1 else "s"
    return color, f"{emoji} Still searching (missing {still_missing} teammate{plural})"


def _format_answer(value: str | list[str]) -> str:
    return ", ".join(value) if isinstance(value, list) else value


def _format_answer_line(question: Question, value: str | list[str]) -> str:
    """Multi-line answers (e.g. contact info) list as their own block, with
    even the first line starting on a new line instead of right after the
    question text."""
    formatted = _format_answer(value)
    if "\n" in formatted:
        return f"**{question.text}**\n{formatted}"
    return f"**{question.text}** {formatted}"


class OtherModal(discord.ui.Modal, title="Other"):
    value = discord.ui.TextInput(label="What else?", max_length=100)

    def __init__(self, on_submitted) -> None:
        super().__init__()
        self.on_submitted = on_submitted

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.on_submitted(interaction, self.value.value)


def _summary_text(answers: dict[str, str | list[str]]) -> str:
    lines = (_format_answer_line(q, answers[q.id]) for q in QUESTIONS if q.id in answers)
    return (
        "Here's what you answered:\n\n"
        f"{chr(10).join(lines)}\n\n"
        "Matches go out after the signup deadline passes."
    )


def _save_and_show_summary(
    user_id: int, guild_id: int, answers: dict[str, str | list[str]], preadded_ids: list[int] | None = None
) -> tuple[str, "SummaryView"]:
    conn = storage.connect()
    if preadded_ids is None:
        # Not passed by an edit-one-answer flow (which doesn't track this) --
        # keep whatever was already on file instead of wiping it out.
        existing = storage.load_profile(conn, user_id, guild_id)
        preadded_ids = existing.preadded_teammate_ids if existing else []
    storage.save_profile(
        conn, Profile(user_id=user_id, answers=answers, preadded_teammate_ids=preadded_ids), guild_id
    )
    content = _summary_text(answers)
    if preadded_ids:
        storage.merge_teams(conn, [user_id] + preadded_ids, guild_id)
        mentions = " ".join(f"<@{uid}>" for uid in preadded_ids)
        content = f"Your team already includes {mentions}.\n\n{content}"
    return content, SummaryView(user_id, guild_id, answers)


class SummaryView(discord.ui.View):
    """Shown after the quiz is complete; lets the user jump back in to edit one answer."""

    def __init__(self, user_id: int, guild_id: int, answers: dict[str, str | list[str]]) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.guild_id = guild_id
        self.answers = answers

    @discord.ui.button(label="Edit an answer", style=discord.ButtonStyle.secondary)
    async def edit_answer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This quiz isn't yours.", ephemeral=True)
            return
        view = EditPickerView(self.user_id, self.guild_id, self.answers)
        await interaction.response.edit_message(content="Which answer would you like to change?", view=view)


class EditPickerView(discord.ui.View):
    """Lets the user choose which answered question to redo, one button each."""

    def __init__(self, user_id: int, guild_id: int, answers: dict[str, str | list[str]]) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.guild_id = guild_id
        self.answers = answers

        for question in QUESTIONS:
            if question.id not in answers:
                continue
            button = discord.ui.Button(label=question.text[:80], style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(question)
            self.add_item(button)

    def _make_callback(self, question: Question):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This quiz isn't yours.", ephemeral=True)
                return

            async def on_answered(answer_interaction: discord.Interaction, value: str | list[str]) -> None:
                self.answers[question.id] = value
                content, view = _save_and_show_summary(self.user_id, self.guild_id, self.answers)
                await answer_interaction.response.edit_message(content=content, view=view)

            view = QuestionView(self.user_id, question, on_answered, current=self.answers.get(question.id))
            await interaction.response.edit_message(content=question.text, view=view)

        return callback


class QuestionView(discord.ui.View):
    """Renders one question as buttons (Wordle-tile style for multi-select) and
    calls on_answered(interaction, value) once the user has committed an answer."""

    def __init__(
        self,
        user_id: int,
        question: Question,
        on_answered,
        current: str | list[str] | None = None,
    ) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.question = question
        self.on_answered = on_answered
        self.current_text = current if (question.free_text and isinstance(current, str)) else None
        if question.multi:
            self.selected: list[str] = list(current) if current else []
        else:
            self.selected = [current] if current else []
        self._build_buttons()

    def _max_select(self) -> int:
        return self.question.max_select or len(self.question.options)

    def _build_buttons(self) -> None:
        self.clear_items()
        if self.question.free_text:
            label = "Edit contact info" if self.current_text else "Enter contact info"
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            button.callback = self._open_contact_modal
            self.add_item(button)
            return
        custom_values = [v for v in self.selected if v not in self.question.options]
        for option in self.question.options:
            is_other_with_value = option == "Other" and custom_values
            is_selected = option in self.selected or is_other_with_value
            style = discord.ButtonStyle.success if is_selected else discord.ButtonStyle.secondary
            label = f"Other: {custom_values[0]}"[:80] if is_other_with_value else option
            button = discord.ui.Button(label=label, style=style)
            button.callback = self._make_option_callback(option)
            self.add_item(button)
        if self.question.multi:
            next_button = discord.ui.Button(label="Next ➡️", style=discord.ButtonStyle.primary)
            next_button.callback = self._next_callback
            self.add_item(next_button)

    def _make_option_callback(self, option: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This quiz isn't yours.", ephemeral=True)
                return

            if option == "Other":
                await self._handle_other_click(interaction)
                return

            if not self.question.multi:
                await self.on_answered(interaction, option)
                return

            if option in self.selected:
                self.selected.remove(option)
            elif len(self.selected) < self._max_select():
                self.selected.append(option)
            else:
                await interaction.response.send_message(
                    f"You can only pick {self._max_select()}.", ephemeral=True
                )
                return

            self._build_buttons()
            await interaction.response.edit_message(view=self)

        return callback

    async def _next_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This quiz isn't yours.", ephemeral=True)
            return
        if not self.selected:
            await interaction.response.send_message("Pick at least one option first.", ephemeral=True)
            return
        await self.on_answered(interaction, list(self.selected))

    async def _handle_other_click(self, interaction: discord.Interaction) -> None:
        if not self.question.multi:
            await interaction.response.send_modal(OtherModal(self.on_answered))
            return

        custom_values = [v for v in self.selected if v not in self.question.options]
        if custom_values:
            self.selected.remove(custom_values[0])
            self._build_buttons()
            await interaction.response.edit_message(view=self)
            return
        if len(self.selected) >= self._max_select():
            await interaction.response.send_message(
                f"You can only pick {self._max_select()}.", ephemeral=True
            )
            return

        async def on_other_submitted(modal_interaction: discord.Interaction, value: str) -> None:
            self.selected.append(value)
            self._build_buttons()
            await modal_interaction.response.edit_message(view=self)

        await interaction.response.send_modal(OtherModal(on_other_submitted))

    async def _open_contact_modal(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This quiz isn't yours.", ephemeral=True)
            return
        await interaction.response.send_modal(ContactModal(self.on_answered, default=self.current_text))


CONTACT_FIELDS = ["GitHub", "LinkedIn", "Instagram", "Other"]


def _parse_contact(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    parsed = {}
    for line in text.splitlines():
        if ": " in line:
            label, value = line.split(": ", 1)
            parsed[label] = value
    return parsed


class ContactModal(discord.ui.Modal, title="Contact info (fill in at least one)"):
    github = discord.ui.TextInput(label="GitHub", required=False, max_length=100, placeholder="optional")
    linkedin = discord.ui.TextInput(label="LinkedIn", required=False, max_length=100, placeholder="optional")
    instagram = discord.ui.TextInput(label="Instagram", required=False, max_length=100, placeholder="optional")
    other = discord.ui.TextInput(label="Other", required=False, max_length=100, placeholder="optional")

    def __init__(self, on_answered, default: str | None = None) -> None:
        super().__init__()
        self.on_answered = on_answered
        existing = _parse_contact(default)
        for field, label in zip(
            [self.github, self.linkedin, self.instagram, self.other],
            CONTACT_FIELDS,
        ):
            if label in existing:
                field.default = existing[label]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        fields = zip(
            CONTACT_FIELDS,
            [self.github.value, self.linkedin.value, self.instagram.value, self.other.value],
        )
        filled = [f"{label}: {value}" for label, value in fields if value.strip()]
        if not filled:
            await interaction.response.send_message("Fill in at least one field.", ephemeral=True)
            return
        await self.on_answered(interaction, "\n".join(filled))


class GuildPickerView(discord.ui.View):
    """Lets a DM user pick which server's hackathon to act on, for commands
    that have no guild context of their own but the user has signed up in
    more than one server."""

    def __init__(self, user_id: int, options: list[tuple[int, str]], on_chosen) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        self.on_chosen = on_chosen
        for guild_id, name in options:
            button = discord.ui.Button(label=name[:80], style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(guild_id)
            self.add_item(button)

    def _make_callback(self, guild_id: int):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This isn't your menu.", ephemeral=True)
                return
            await self.on_chosen(interaction, guild_id)

        return callback


class TeammateCountView(discord.ui.View):
    """First step of /quiz: how many teammates does this person already have
    who won't be filling out the quiz themselves? Lets a pre-formed group
    sign up as a team from just one person's answers."""

    def __init__(self, user_id: int, guild_id: int) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.guild_id = guild_id
        for count in (0, 1, 2):
            button = discord.ui.Button(label=str(count), style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(count)
            self.add_item(button)

    def _make_callback(self, count: int):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This quiz isn't yours.", ephemeral=True)
                return
            if count == 0:
                await _ask_question(
                    interaction, self.user_id, self.guild_id, 0, {}, is_initial=False, preadded_ids=[]
                )
                return
            await interaction.response.send_modal(PreaddedTeammatesModal(self.user_id, self.guild_id))

        return callback


class PreaddedTeammatesModal(discord.ui.Modal, title="Your existing teammates"):
    """Collects Discord user IDs for teammates the person already has.
    Always offers 3 boxes (1 required, 2 optional) regardless of whether they
    said 1 or 2 above — simplest way to let them fill in exactly as many as
    they actually have."""

    teammate_1 = discord.ui.TextInput(label="Teammate's Discord user ID", max_length=25)
    teammate_2 = discord.ui.TextInput(
        label="2nd teammate's Discord user ID (optional)", required=False, max_length=25
    )
    teammate_3 = discord.ui.TextInput(
        label="3rd teammate's Discord user ID (optional)", required=False, max_length=25
    )

    def __init__(self, user_id: int, guild_id: int) -> None:
        super().__init__()
        self.user_id = user_id
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        preadded_ids: list[int] = []
        for box in (self.teammate_1, self.teammate_2, self.teammate_3):
            raw = box.value.strip()
            if not raw:
                continue
            if not raw.isdigit():
                await interaction.response.send_message(
                    f"'{raw}' isn't a valid Discord user ID (numbers only — with Developer Mode on, "
                    "right-click their name and Copy User ID).",
                    ephemeral=True,
                )
                return
            preadded_ids.append(int(raw))
        await _ask_question(
            interaction, self.user_id, self.guild_id, 0, {}, is_initial=False, preadded_ids=preadded_ids
        )


async def _require_guild(interaction: discord.Interaction) -> bool:
    """False (after replying with an error) if this command was run somewhere
    with no server context (a DM, or a user-installed context) — every
    command here needs a guild_id to scope its data to the right hackathon."""
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "This command only works inside a server, not in a DM.", ephemeral=True
        )
        return False
    return True


async def _ask_question(
    interaction: discord.Interaction,
    user_id: int,
    guild_id: int,
    index: int,
    answers: dict[str, str | list[str]],
    is_initial: bool,
    preadded_ids: list[int] | None = None,
) -> None:
    question = QUESTIONS[index]

    async def on_answered(answer_interaction: discord.Interaction, value: str | list[str]) -> None:
        answers[question.id] = value
        if index + 1 < len(QUESTIONS):
            await _ask_question(
                answer_interaction, user_id, guild_id, index + 1, answers, is_initial=False,
                preadded_ids=preadded_ids,
            )
        else:
            content, view = _save_and_show_summary(user_id, guild_id, answers, preadded_ids)
            await answer_interaction.response.edit_message(
                content=f"You're signed up! {content}", view=view
            )

    view = QuestionView(user_id, question, on_answered)
    if is_initial:
        await interaction.response.send_message(question.text, view=view, ephemeral=True)
    else:
        await interaction.response.edit_message(content=question.text, view=view)


def create_client(signup_deadline: datetime, dev_guild_id: int | None = None) -> discord.Client:
    intents = discord.Intents.default()
    intents.reactions = True
    intents.members = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    @client.event
    async def on_ready() -> None:
        print(f"Logged in as {client.user}")
        # Global sync so every server the bot is in (including ones it joins
        # later) gets commands, even though Discord can take up to an hour to
        # propagate that. If DEV_GUILD_ID is set, that one server additionally
        # gets an instant guild-specific copy for fast local iteration — while
        # both are live, that one server may briefly show a duplicate entry
        # until the global version either propagates or the two converge.
        await tree.sync()
        if dev_guild_id is not None:
            guild = discord.Object(id=dev_guild_id)
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            print(f"Synced globally, plus instantly to dev guild {dev_guild_id} for fast iteration")
        else:
            print("Synced commands globally")
        if not matching_check.is_running():
            matching_check.start()

    @tree.command(name="quiz", description="Fill out the HackMatch teammate quiz")
    async def quiz(interaction: discord.Interaction) -> None:
        if not await _require_guild(interaction):
            return
        conn = storage.connect()
        deadline = _effective_deadline(conn, interaction.guild_id, signup_deadline)
        if datetime.now(timezone.utc) >= deadline:
            await interaction.response.send_message(
                "Signups are closed — matching has already run.", ephemeral=True
            )
            return
        view = TeammateCountView(interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(
            "How many other teammate(s) do you already have on your team?", view=view, ephemeral=True
        )

    @tree.command(name="unpair", description="Leave your current team and go back to searching solo")
    async def unpair(interaction: discord.Interaction) -> None:
        if not await _require_guild(interaction):
            return
        conn = storage.connect()
        remaining = storage.leave_team(conn, interaction.user.id, interaction.guild_id)
        if not remaining:
            await interaction.response.send_message(
                "You're not currently paired with anyone.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "You've left your team and are searching solo again.", ephemeral=True
        )
        leaver = await client.fetch_user(interaction.user.id)
        for member_id in remaining:
            try:
                user = await client.fetch_user(member_id)
                await user.send(f"⚠️ {leaver.mention} has left your team.")
            except (discord.NotFound, discord.Forbidden):
                continue

    @tree.command(name="my_team", description="See who's currently on your team")
    async def my_team(interaction: discord.Interaction) -> None:
        if not await _require_guild(interaction):
            return
        conn = storage.connect()
        guild_id = interaction.guild_id
        if storage.load_profile(conn, interaction.user.id, guild_id) is None:
            await interaction.response.send_message("You haven't filled out /quiz yet.", ephemeral=True)
            return

        member_ids = storage.get_team_members(conn, interaction.user.id, guild_id)
        others = [uid for uid in member_ids if uid != interaction.user.id]
        if not others:
            await interaction.response.send_message(
                "You're currently searching solo — no teammates yet.", ephemeral=True
            )
            return

        lines = []
        for uid in others:
            try:
                mention = (await client.fetch_user(uid)).mention
            except discord.HTTPException:
                mention = f"<@{uid}>"
            profile = storage.load_profile(conn, uid, guild_id)
            contact = profile.answers.get("contact") if profile else None
            contact_line = f" — {contact}" if contact else ""
            lines.append(f"{mention}{contact_line}")

        remaining = MAX_TEAM_SIZE - len(member_ids)
        slots_line = (
            "Your team is full." if remaining <= 0
            else f"Still looking for {remaining} more teammate{'' if remaining == 1 else 's'}."
        )
        await interaction.response.send_message(
            f"Your team ({len(member_ids)}/{MAX_TEAM_SIZE}):\n" + "\n".join(lines) + f"\n\n{slots_line}",
            ephemeral=True,
        )

    async def _send_fresh_candidates(interaction: discord.Interaction, guild_id: int) -> None:
        """Shared by /run_matching and /more_matches: a fresh batch of
        candidates for just the caller's own team. Assumes the interaction
        has already been deferred."""
        conn = storage.connect()
        if storage.load_profile(conn, interaction.user.id, guild_id) is None:
            await interaction.followup.send("You haven't filled out /quiz yet.", ephemeral=True)
            return

        member_ids = storage.get_team_members(conn, interaction.user.id, guild_id)
        if len(member_ids) >= MAX_TEAM_SIZE:
            await interaction.followup.send("Your team is already full.", ephemeral=True)
            return

        teams = storage.load_teams_with_capacity(conn, guild_id, MAX_TEAM_SIZE)
        teams_by_id = {min(p.user_id for p in t): t for t in teams}
        own_team_id = min(member_ids)
        team = teams_by_id.get(own_team_id)
        if team is None or not any(tid != own_team_id for tid in teams_by_id):
            await interaction.followup.send("No one else is available to match with right now.", ephemeral=True)
            return

        best_global_team = compute_best_global_team_matches(teams, QUESTIONS, max_combined_size=MAX_TEAM_SIZE)
        await _post_candidates_for_team(client, conn, team, teams_by_id, best_global_team, guild_id, set())
        await interaction.followup.send("Sent a fresh batch of candidates to your team!", ephemeral=True)

    @tree.command(
        name="run_matching",
        description="(Admin) Run matchmaking now for everyone, even before the signup deadline",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def run_matching_command(interaction: discord.Interaction) -> None:
        if not await _require_guild(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        conn = storage.connect()
        teams_notified = await run_matching(client, interaction.guild_id)
        storage.mark_matching_run(conn, interaction.guild_id)
        if teams_notified == 0:
            await interaction.followup.send(
                "Matching ran, but no DMs went out — everyone signed up is already on the same team, "
                "or there's nobody else left to match with.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"Matching complete — DMs sent to {teams_notified} team(s).", ephemeral=True
            )

    @tree.command(
        name="more_matches",
        description="Not interested in your current candidates? Get a fresh batch. (DM only)",
    )
    async def more_matches(interaction: discord.Interaction) -> None:
        if interaction.guild_id is not None:
            await interaction.response.send_message(
                "This command only works in a DM with me, not in a server — use /run_matching there instead.",
                ephemeral=True,
            )
            return

        conn = storage.connect()
        guild_ids = storage.get_guilds_for_user(conn, interaction.user.id)
        if not guild_ids:
            await interaction.response.send_message("You haven't filled out /quiz in any server yet.", ephemeral=True)
            return

        if len(guild_ids) == 1:
            await interaction.response.defer(ephemeral=True)
            await _send_fresh_candidates(interaction, guild_ids[0])
            return

        async def on_chosen(picker_interaction: discord.Interaction, guild_id: int) -> None:
            await picker_interaction.response.defer(ephemeral=True)
            await _send_fresh_candidates(picker_interaction, guild_id)

        options = [(gid, (client.get_guild(gid).name if client.get_guild(gid) else f"Server {gid}")) for gid in guild_ids]
        view = GuildPickerView(interaction.user.id, options, on_chosen)
        await interaction.response.send_message("Which server's matches do you want?", view=view, ephemeral=True)

    @tree.command(
        name="set_deadline",
        description="(Admin) Set this server's signup deadline",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(deadline="ISO datetime, e.g. 2026-12-05T18:00:00+00:00")
    async def set_deadline(interaction: discord.Interaction, deadline: str) -> None:
        if not await _require_guild(interaction):
            return
        try:
            parsed = datetime.fromisoformat(deadline)
        except ValueError:
            await interaction.response.send_message(
                "Couldn't parse that. Use ISO format, e.g. 2026-12-05T18:00:00+00:00", ephemeral=True
            )
            return
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        conn = storage.connect()
        storage.set_guild_deadline(conn, interaction.guild_id, parsed.isoformat())
        await interaction.response.send_message(
            f"This server's signup deadline is now {parsed.isoformat()}.", ephemeral=True
        )

    async def _admin_permission_error_handler(
        interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need administrator permission to do that.", ephemeral=True
            )
        else:
            raise error

    run_matching_command.error(_admin_permission_error_handler)
    set_deadline.error(_admin_permission_error_handler)

    @client.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
        if client.user is not None and payload.user_id == client.user.id:
            return
        if str(payload.emoji) != MATCH_EMOJI:
            return

        conn = storage.connect()
        proposal = storage.get_team_proposal(conn, payload.message_id)
        if proposal is None:
            return
        guild_id, reactor_id, own_team_key, candidate_key = proposal
        if payload.user_id != reactor_id:
            return

        own_members = [int(uid) for uid in own_team_key.split(",")]
        candidate_members = [int(uid) for uid in candidate_key.split(",")]
        stale = (
            len(own_members) + len(candidate_members) > MAX_TEAM_SIZE
            or storage.team_key_of(conn, reactor_id, guild_id) != own_team_key
            or storage.team_key_of(conn, candidate_members[0], guild_id) != candidate_key
        )
        if stale:
            # Someone's team changed (grew/merged) or is already full since this
            # was posted; update the embed's status color/text to reflect the
            # candidate team's current state instead of silently ignoring it.
            try:
                channel = client.get_channel(payload.channel_id) or await client.fetch_channel(
                    payload.channel_id
                )
                message = await channel.fetch_message(payload.message_id)
                if message.embeds:
                    current_size = len(storage.get_team_members(conn, candidate_members[0], guild_id))
                    color, status_text = _status_color_and_text(current_size)
                    embed = message.embeds[0]
                    # Keep every line except the last (always the status line) and replace that one.
                    kept_lines = (embed.description or "").splitlines()[:-1]
                    embed.description = "\n".join(kept_lines + [status_text])
                    embed.colour = color
                    await message.edit(embed=embed)
            except (discord.NotFound, discord.Forbidden):
                pass
            return

        storage.record_team_approval(conn, reactor_id, guild_id, candidate_key)
        # Teammates preadded by someone else (never filled out /quiz themselves)
        # never get sent a proposal to react to, so don't require their approval.
        own_approvers = [uid for uid in own_members if storage.load_profile(conn, uid, guild_id) is not None]
        candidate_approvers = [
            uid for uid in candidate_members if storage.load_profile(conn, uid, guild_id) is not None
        ]
        own_fully_approved = storage.team_fully_approved(conn, own_approvers, guild_id, candidate_key)
        candidate_fully_approved = storage.team_fully_approved(conn, candidate_approvers, guild_id, own_team_key)

        if own_fully_approved and candidate_fully_approved:
            new_members = own_members + candidate_members
            storage.merge_teams(conn, new_members, guild_id)
            await _notify_team_match(client, own_members, candidate_members, guild_id)
            await _refresh_stale_postings(
                client, conn, [own_team_key, candidate_key], len(new_members), guild_id
            )
            await _continue_matching_after_merge(client, conn, new_members, guild_id)

    @tasks.loop(minutes=5)
    async def matching_check() -> None:
        for guild in client.guilds:
            conn = storage.connect()
            deadline = _effective_deadline(conn, guild.id, signup_deadline)
            if datetime.now(timezone.utc) < deadline or storage.has_matching_run(conn, guild.id):
                continue
            await run_matching(client, guild.id)
            storage.mark_matching_run(conn, guild.id)

    return client


async def _notify_team_match(
    client: discord.Client, team_a: list[int], team_b: list[int], guild_id: int
) -> None:
    """Sends every member of the newly-merged team (both sides, including any
    pre-existing teammates) an updated "you are paired with" roster of everyone
    else now on their team. Resilient to a failure on any one person (e.g. a
    transient network error) so it doesn't stop the rest of the team from
    being notified."""
    conn = storage.connect()
    all_members = team_a + team_b
    profiles = {uid: storage.load_profile(conn, uid, guild_id) for uid in all_members}

    for member_id in all_members:
        lines = []
        for other_id in all_members:
            if other_id == member_id:
                continue
            try:
                mention = (await client.fetch_user(other_id)).mention
            except discord.HTTPException:
                mention = f"<@{other_id}>"
            other_profile = profiles.get(other_id)
            contact = other_profile.answers.get("contact") if other_profile else None
            contact_line = f" — {contact}" if contact else ""
            lines.append(f"{mention}{contact_line}")
        try:
            user = await client.fetch_user(member_id)
            await user.send("\U0001f389 You are paired with:\n" + "\n".join(lines))
        except discord.HTTPException:
            continue


async def _team_candidate_embed(
    client: discord.Client,
    candidate_team: list[Profile],
    score: float,
    guild_name: str,
    is_best_match: bool = False,
) -> discord.Embed:
    # No title -- Discord embed titles can't render mentions as clickable, so
    # every person's name only ever appears as a mention, in the description
    # or a field's value (both of which do render mentions as clickable).
    mentions = []
    for member in candidate_team:
        if member.is_preadded_placeholder:
            mentions.append(f"<@{member.user_id}>")
            continue
        try:
            member_user = await client.fetch_user(member.user_id)
            mentions.append(member_user.mention)
        except discord.NotFound:
            mentions.append(f"(left the server: {member.user_id})")

    mention_people = ", ".join(mentions)
    color, status_text = _status_color_and_text(len(candidate_team))
    description_lines = (
        [f"## {guild_name}", f"\U0001f31f Your best match: {mention_people}"] if is_best_match else []
    )
    description_lines += [f"Compatibility: {score:.0%}", status_text]
    embed = discord.Embed(description="\n".join(description_lines), color=color)

    # Show full detail for just one representative member (avoids a wall of
    # repeated fields for bigger teams); the rest are named, not spelled out.
    # Nothing is removed from storage — this only trims what's displayed here.
    primary_member, primary_mention = candidate_team[0], mentions[0]
    lines = [primary_mention] + [
        f"**{q.text}** {_format_answer(primary_member.answers[q.id])}"
        for q in QUESTIONS
        if q.id in primary_member.answers and not q.free_text
    ]
    embed.add_field(name="Contact", value="\n".join(lines), inline=False)

    other_members = list(zip(candidate_team[1:], mentions[1:]))
    if other_members:
        teammate_lines = []
        for member, mention in other_members:
            if member.is_preadded_placeholder:
                teammate_lines.append(f"{mention} (already on this team)")
                continue
            summary = ", ".join(
                _format_answer(member.answers[q.id])
                for q in QUESTIONS
                if q.id in member.answers and not q.free_text
            )
            teammate_lines.append(f"{mention}: {summary}" if summary else f"{mention}: No answers")
        embed.add_field(name="Also on this team", value="\n".join(teammate_lines), inline=False)

    return embed


async def _post_candidates_for_team(
    client: discord.Client,
    conn: object,
    team: list[Profile],
    teams_by_id: dict[int, list[Profile]],
    best_global_team: dict[int, int],
    guild_id: int,
    already_posted: set[tuple[int, int]],
) -> int:
    """DMs every current member of `team` their top candidate teams out of
    `teams_by_id`, with their globally-best-overall match featured first.
    Each candidate also gets a reciprocal proposal back (see below).
    already_posted tracks which team-pairs have already been messaged in this
    matching round (shared across calls for other teams too), so a pair never
    gets double-messaged just because the bulk loop reaches both sides of it.
    Returns how many new candidate teams `team` was actually shown."""
    team_id = min(p.user_id for p in team)
    other_teams = [
        t for i, t in teams_by_id.items()
        if i != team_id and len(t) + len(team) <= MAX_TEAM_SIZE
    ]
    ranked = sorted(
        other_teams, key=lambda other: team_compatibility_score(team, other, QUESTIONS), reverse=True
    )

    best_id = best_global_team.get(team_id)
    ordered = ranked
    if best_id is not None and best_id in teams_by_id:
        best = teams_by_id[best_id]
        if len(best) + len(team) <= MAX_TEAM_SIZE:
            ordered = [best] + [t for t in ranked if min(p.user_id for p in t) != best_id]

    own_key = storage.team_key([p.user_id for p in team])
    sent_count = 0
    guild = client.get_guild(guild_id)
    if guild is None:
        try:
            guild = await client.fetch_guild(guild_id)
        except discord.HTTPException:
            guild = None
    guild_name = guild.name if guild else "this server"

    for candidate_team in ordered:
        if sent_count >= TOP_CANDIDATES_PER_USER:
            break
        candidate_team_id = min(p.user_id for p in candidate_team)
        pair_key = (min(team_id, candidate_team_id), max(team_id, candidate_team_id))
        if pair_key in already_posted:
            continue
        already_posted.add(pair_key)
        sent_count += 1

        candidate_key = storage.team_key([p.user_id for p in candidate_team])
        score = team_compatibility_score(team, candidate_team, QUESTIONS)

        embed = await _team_candidate_embed(
            client, candidate_team, score, guild_name, is_best_match=candidate_team_id == best_id
        )
        for member in team:
            if member.is_preadded_placeholder:
                continue  # never signed up themselves; can't react to approve
            try:
                user = await client.fetch_user(member.user_id)
                message = await user.send(embed=embed)
            except (discord.NotFound, discord.Forbidden):
                continue
            await message.add_reaction(MATCH_EMOJI)
            storage.record_team_proposal(conn, message.id, guild_id, member.user_id, own_key, candidate_key)

        # Reciprocal: whoever gets shown as a candidate also gets shown the
        # team that's looking at them, in this same step. Without this, the
        # candidate side might never independently rank/see this team and so
        # could never approve the exact combined key a merge requires.
        reciprocal_is_best = best_global_team.get(candidate_team_id) == team_id
        reciprocal_embed = await _team_candidate_embed(
            client, team, score, guild_name, is_best_match=reciprocal_is_best
        )
        for member in candidate_team:
            if member.is_preadded_placeholder:
                continue  # never signed up themselves; can't react to approve
            try:
                user = await client.fetch_user(member.user_id)
                message = await user.send(embed=reciprocal_embed)
            except (discord.NotFound, discord.Forbidden):
                continue
            await message.add_reaction(MATCH_EMOJI)
            storage.record_team_proposal(conn, message.id, guild_id, member.user_id, candidate_key, own_key)

    return sent_count


async def run_matching(client: discord.Client, guild_id: int) -> int:
    """Score every team in this server (solo signups start as a team of 1)
    against every other team with spare capacity, and DM every current member
    of each team their top candidate teams, with the globally-best-overall
    match (maximizing total compatibility across everyone, not just each
    team's own ranking) featured first. Returns how many teams were shown at
    least one candidate (0 means nobody had anyone left to be matched with)."""
    conn = storage.connect()
    teams = storage.load_teams_with_capacity(conn, guild_id, MAX_TEAM_SIZE)
    teams_by_id = {min(p.user_id for p in team): team for team in teams}
    best_global_team = compute_best_global_team_matches(teams, QUESTIONS, max_combined_size=MAX_TEAM_SIZE)

    already_posted: set[tuple[int, int]] = set()
    teams_notified = 0
    for team in teams:
        sent = await _post_candidates_for_team(client, conn, team, teams_by_id, best_global_team, guild_id, already_posted)
        if sent > 0:
            teams_notified += 1
    return teams_notified


async def _continue_matching_after_merge(
    client: discord.Client, conn: object, team_member_ids: list[int], guild_id: int
) -> None:
    """After a merge, keep searching for the newly-formed team automatically,
    unless it's already full."""
    if len(team_member_ids) >= MAX_TEAM_SIZE:
        return
    teams = storage.load_teams_with_capacity(conn, guild_id, MAX_TEAM_SIZE)
    teams_by_id = {min(p.user_id for p in t): t for t in teams}
    team = teams_by_id.get(min(team_member_ids))
    if team is None:
        return
    best_global_team = compute_best_global_team_matches(teams, QUESTIONS, max_combined_size=MAX_TEAM_SIZE)
    await _post_candidates_for_team(client, conn, team, teams_by_id, best_global_team, guild_id, set())


async def _refresh_stale_postings(
    client: discord.Client,
    conn: object,
    old_candidate_keys: list[str],
    new_size: int,
    guild_id: int,
) -> None:
    """After a merge, silently update any other pending candidate posts that
    were still showing one of the old (pre-merge) teams, so whoever's looking
    at them sees the current status instead of an outdated one."""
    color, status_text = _status_color_and_text(new_size)
    seen_messages: set[int] = set()
    for old_key in old_candidate_keys:
        for message_id, reactor_id in storage.get_proposals_for_candidate(conn, guild_id, old_key):
            if message_id in seen_messages:
                continue
            seen_messages.add(message_id)
            try:
                user = await client.fetch_user(reactor_id)
                channel = user.dm_channel or await user.create_dm()
                message = await channel.fetch_message(message_id)
                if message.embeds:
                    embed = message.embeds[0]
                    # Keep every line except the last (always the status line) and replace that one.
                    kept_lines = (embed.description or "").splitlines()[:-1]
                    embed.description = "\n".join(kept_lines + [status_text])
                    embed.colour = color
                    await message.edit(embed=embed)
            except (discord.NotFound, discord.Forbidden):
                continue
