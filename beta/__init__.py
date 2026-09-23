import discord
from discord.ext import commands
from redbot.core import commands, Config
from redbot.core.bot import Red
import re
import asyncio
import logging

log = logging.getLogger("red.whitelist")

# V2 Import
try:
    from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Button
    V2_AVAILABLE = True
except ImportError:
    V2_AVAILABLE = False


# ============================================================
# BUTTONS (persistent durch custom_id)
# ============================================================

class StartApplicationButton(Button):
    def __init__(self):
        super().__init__(
            label="Bewerbung starten",
            style=discord.ButtonStyle.primary,
            custom_id="fivem_wl_start_v11",
            emoji="📝"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FiveMWhitelist")
        if not cog:
            return

        # Blacklist-Check
        blacklist = await cog.config.guild(interaction.guild).blacklist()
        if interaction.user.id in blacklist:
            return await interaction.response.send_message(
                "🚫 **Du befindest dich auf der Blacklist!**\n"
                "Du wurdest von der Whitelist-Bewerbung ausgeschlossen. Bei Fragen wende dich an ein Teammitglied.",
                ephemeral=True
            )

        # WL-Check
        wl_role_id = await cog.config.guild(interaction.guild).wl_role()
        if wl_role_id:
            wl_role = interaction.guild.get_role(wl_role_id)
            if wl_role and wl_role in interaction.user.roles:
                return await interaction.response.send_message("Du bist bereits gewhitelisted! 🎉", ephemeral=True)

        await interaction.response.send_modal(WhitelistModal(cog.config))


class AcceptButton(Button):
    def __init__(self):
        super().__init__(
            label="Annehmen",
            style=discord.ButtonStyle.success,
            custom_id="fivem_wl_accept_v11",
            emoji="✅"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FiveMWhitelist")
        if not cog:
            return
        if not await cog.check_perm_interaction(interaction):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        data = await cog.get_application(interaction)
        if not data:
            return await interaction.response.send_message("❌ Bewerbung nicht gefunden.", ephemeral=True)

        applicant = interaction.guild.get_member(data["user_id"])
        wl_role_id = await cog.config.guild(interaction.guild).wl_role()
        wl_role = interaction.guild.get_role(wl_role_id) if wl_role_id else None

        if not wl_role:
            return await interaction.response.send_message("❌ Whitelist-Rolle nicht konfiguriert.", ephemeral=True)
        if not applicant:
            return await interaction.response.send_message("❌ Bewerber nicht mehr auf dem Server.", ephemeral=True)

        try:
            await applicant.add_roles(wl_role, reason=f"Whitelist angenommen von {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Keine Berechtigung, die Rolle zu vergeben.", ephemeral=True)

        dm_failed = False
        try:
            await applicant.send(
                f"🎉 **Herzlichen Glückwunsch!**\n"
                f"Deine Whitelist-Bewerbung auf **{interaction.guild.name}** wurde angenommen!"
            )
        except discord.Forbidden:
            dm_failed = True

        # Status in Config
        await cog.set_application_status(interaction, "accepted", interaction.user)

        # View neu aufbauen
        new_view = cog.build_application_view(data, status="accepted", admin=interaction.user)
        await interaction.response.edit_message(view=new_view)

        if dm_failed:
            await interaction.followup.send("⚠️ User angenommen, aber DMs gesperrt.", ephemeral=True)


class RejectButton(Button):
    def __init__(self):
        super().__init__(
            label="Ablehnen",
            style=discord.ButtonStyle.danger,
            custom_id="fivem_wl_reject_v11",
            emoji="❌"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FiveMWhitelist")
        if not cog:
            return
        if not await cog.check_perm_interaction(interaction):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        data = await cog.get_application(interaction)
        if not data:
            return await interaction.response.send_message("❌ Bewerbung nicht gefunden.", ephemeral=True)

        applicant = interaction.guild.get_member(data["user_id"])
        if not applicant:
            return await interaction.response.send_message("❌ Bewerber nicht mehr auf dem Server.", ephemeral=True)

        await interaction.response.send_modal(
            RejectReasonModal(cog, applicant, interaction.message, interaction.guild.name, interaction.user, data)
        )


class QuestionsButton(Button):
    def __init__(self):
        super().__init__(
            label="Rückfragen",
            style=discord.ButtonStyle.secondary,
            custom_id="fivem_wl_questions_v11",
            emoji="❓"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FiveMWhitelist")
        if not cog:
            return
        if not await cog.check_perm_interaction(interaction):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        data = await cog.get_application(interaction)
        if not data:
            return await interaction.response.send_message("❌ Bewerbung nicht gefunden.", ephemeral=True)

        applicant = interaction.guild.get_member(data["user_id"])
        if not applicant:
            return await interaction.response.send_message("❌ Bewerber nicht mehr auf dem Server.", ephemeral=True)

        try:
            await applicant.send(
                f"❓ **Rückfragen zu deiner Bewerbung**\n\n"
                f"Hallo {applicant.mention}, wir haben noch ein paar Fragen zu deiner Whitelist-Anfrage. "
                f"Bitte komm in den Support-Warteraum oder eröffne ein Ticket."
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                "⚠️ Der User hat DMs gesperrt. Bitte anderweitig kontaktieren.", ephemeral=True
            )

        await cog.set_application_status(interaction, "questions", interaction.user)
        new_view = cog.build_application_view(data, status="questions", admin=interaction.user)
        await interaction.response.edit_message(view=new_view)


# ============================================================
# VIEWS
# ============================================================

class WhitelistButtonView(LayoutView):
    def __init__(self, config: Config):
        super().__init__(timeout=None)
        self.config = config

        container = Container(accent_color=discord.Color.dark_blue())
        container.add_item(TextDisplay("## 🚨 FiveM Whitelist Bewerbung"))
        container.add_item(TextDisplay(
            "Willkommen auf unserem Server!\n\n"
            "Um auf unseren Server zu kommen und die Whitelist zu erhalten, "
            "musst du ein kurzes Formular ausfüllen."
        ))
        container.add_item(Separator())
        container.add_item(TextDisplay("-# Klicke unten auf den Button, um deine Bewerbung zu starten."))

        row = ActionRow()
        row.add_item(StartApplicationButton())
        container.add_item(row)

        self.add_item(container)


class ApplicationActionsView(LayoutView):
    """Wird nur für die Registrierung der Buttons beim Bot-Start gebraucht."""
    def __init__(self, config: Config):
        super().__init__(timeout=None)
        self.config = config

        container = Container(accent_color=discord.Color.orange())
        container.add_item(TextDisplay("### Bewerbungs-Aktionen"))
        row = ActionRow()
        row.add_item(AcceptButton())
        row.add_item(RejectButton())
        row.add_item(QuestionsButton())
        container.add_item(row)
        self.add_item(container)


# ============================================================
# MODALS
# ============================================================

class WhitelistModal(discord.ui.Modal, title="FiveM Whitelist Bewerbung"):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config

    ooc_name = discord.ui.TextInput(
        label="Dein Name (OOC)",
        placeholder="Dein echter Vorname (z.B. Max)",
        required=True,
        max_length=30
    )
    alter = discord.ui.TextInput(
        label="Dein Alter (OOC)",
        placeholder="z.B. 22",
        required=True,
        min_length=2,
        max_length=3
    )
    rp_erfahrung = discord.ui.TextInput(
        label="Deine Roleplay-Erfahrung",
        placeholder="Seit wann spielst du RP? Welche Server?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )
    ic_plans = discord.ui.TextInput(
        label="Was planst du auf dem Server? (IC)",
        placeholder="z.B. Polizei, Arzt, Gangmitglied, Mechaniker...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )
    charakter_geschichte = discord.ui.TextInput(
        label="Deine Charakter-Geschichte",
        placeholder="Erzähl uns kurz die Hintergrundgeschichte deines Characters...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FiveMWhitelist")
        if not cog:
            return

        log_channel_id = await self.config.guild(interaction.guild).log_channel()
        if not log_channel_id:
            return await interaction.response.send_message("Fehler: Kein Log-Channel gesetzt.", ephemeral=True)

        log_channel = interaction.guild.get_channel(log_channel_id)
        if not log_channel:
            return await interaction.response.send_message("Fehler: Log-Channel nicht gefunden.", ephemeral=True)

        data = {
            "user_id": interaction.user.id,
            "ooc_name": self.ooc_name.value,
            "alter": self.alter.value,
            "rp_erfahrung": self.rp_erfahrung.value,
            "ic_plans": self.ic_plans.value,
            "charakter_geschichte": self.charakter_geschichte.value,
            "status": "pending",
        }

        # Ping-Content bauen
        ping_role_id = await self.config.guild(interaction.guild).ping_role()
        content = None
        if ping_role_id:
            role = interaction.guild.get_role(ping_role_id)
            if role:
                content = f"🔔 Neue Bewerbung von {interaction.user.mention}\n{role.mention}"

        # V2 View bauen
        view = cog.build_application_view(data, status="pending")

        try:
            msg = await log_channel.send(content=content, view=view)
        except Exception as e:
            log.error(f"[Whitelist] Fehler beim Senden der Bewerbung: {e}")
            return await interaction.response.send_message("❌ Fehler beim Senden an das Team.", ephemeral=True)

        # Daten in Config speichern (persistent für Buttons nach Neustart)
        async with self.config.guild(interaction.guild).applications() as apps:
            apps[str(msg.id)] = data

        await interaction.response.send_message(
            "✅ Deine Bewerbung wurde erfolgreich an das Team gesendet! Bitte habe etwas Geduld.",
            ephemeral=True
        )


class RejectReasonModal(discord.ui.Modal, title="Grund für Ablehnung"):
    def __init__(self, cog, applicant, original_message, guild_name, admin, data):
        super().__init__()
        self.cog = cog
        self.applicant = applicant
        self.original_message = original_message
        self.guild_name = guild_name
        self.admin = admin
        self.data = data

    reason = discord.ui.TextInput(
        label="Warum wird der Bewerber abgelehnt?",
        placeholder="z.B. Alter passt nicht, unzureichende Antwort...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        dm_failed = False
        try:
            await self.applicant.send(
                f"❌ **Bedauerlicherweise...**\n"
                f"Deine Whitelist-Bewerbung auf **{self.guild_name}** wurde leider abgelehnt.\n\n"
                f"**Grund:** {self.reason.value}\n\n"
                f"Du kannst es gerne erneut versuchen."
            )
        except discord.Forbidden:
            dm_failed = True

        # Status speichern
        async with self.cog.config.guild(interaction.guild).applications() as apps:
            key = str(self.original_message.id)
            if key in apps:
                apps[key]["status"] = "rejected"
                apps[key]["decided_by"] = self.admin.id
                apps[key]["reason"] = self.reason.value

        # View neu aufbauen mit Ablehnung
        new_view = self.cog.build_application_view(
            self.data, status="rejected", admin=self.admin, reason=self.reason.value
        )
        await self.original_message.edit(view=new_view)

        if dm_failed:
            await interaction.response.send_message(
                "⚠️ User abgelehnt, aber DMs gesperrt.", ephemeral=True
            )
        else:
            await interaction.response.send_message("✅ Bewerber abgelehnt und informiert.", ephemeral=True)


# ============================================================
# HAUPT-COG
# ============================================================

class FiveMWhitelist(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)

        default_guild = {
            "log_channel": None,
            "wl_role": None,
            "ping_role": None,
            "extra_wl_roles": [],
            "blacklist": [],
            "blacklist_channel": None,
            "applications": {},  # msg_id (str) → {user_id, ooc_name, ..., status}
        }
        self.config.register_guild(**default_guild)

        # Persistente Views registrieren
        if V2_AVAILABLE:
            self.bot.add_view(WhitelistButtonView(self.config))
            self.bot.add_view(ApplicationActionsView(self.config))

    # ---------------- HELPER ----------------

    def build_application_view(self, data: dict, status: str = "pending", admin: discord.Member = None, reason: str = None):
        """Baut die Log-Nachricht je nach Status auf."""
        if not V2_AVAILABLE:
            # Fallback: klassisches Embed (nur wenn V2 nicht verfügbar)
            return None

        colors = {
            "pending": discord.Color.orange(),
            "accepted": discord.Color.green(),
            "rejected": discord.Color.red(),
            "questions": discord.Color.gold(),
        }
        titles = {
            "pending": "📋 Neue Whitelist Bewerbung",
            "accepted": "✅ Angenommen",
            "rejected": "❌ Abgelehnt",
            "questions": "❓ Rückfragen gestellt",
        }

        view = LayoutView()
        container = Container(accent_color=colors.get(status, discord.Color.orange()))

        container.add_item(TextDisplay(f"## {titles.get(status, '📋 Bewerbung')}"))
        container.add_item(TextDisplay(f"**Bewerber:** <@{data['user_id']}> (`{data['user_id']}`)"))
        container.add_item(Separator())

        container.add_item(TextDisplay(f"**Name (OOC):** {data['ooc_name']}"))
        container.add_item(TextDisplay(f"**Alter (OOC):** {data['alter']}"))
        container.add_item(TextDisplay(f"**Roleplay-Erfahrung:**\n{data['rp_erfahrung']}"))
        container.add_item(TextDisplay(f"**IC Pläne:**\n{data['ic_plans']}"))
        container.add_item(TextDisplay(f"**Charakter-Geschichte:**\n{data['charakter_geschichte']}"))
        container.add_item(Separator())

        if status == "pending":
            row = ActionRow()
            row.add_item(AcceptButton())
            row.add_item(RejectButton())
            row.add_item(QuestionsButton())
            container.add_item(row)
        else:
            if status == "accepted":
                container.add_item(TextDisplay(f"### ✅ Angenommen von {admin.mention if admin else 'Unbekannt'}"))
            elif status == "rejected":
                reason_text = f"\n**Grund:** {reason}" if reason else ""
                container.add_item(TextDisplay(f"### ❌ Abgelehnt von {admin.mention if admin else 'Unbekannt'}{reason_text}"))
            elif status == "questions":
                container.add_item(TextDisplay(f"### ❓ Rückfragen gestellt von {admin.mention if admin else 'Unbekannt'}"))

        view.add_item(container)
        return view

    async def get_application(self, interaction: discord.Interaction):
        """Holt Bewerbungsdaten anhand der Message-ID."""
        msg_id = str(interaction.message.id)
        apps = await self.config.guild(interaction.guild).applications()
        return apps.get(msg_id)

    async def set_application_status(self, interaction: discord.Interaction, status: str, admin: discord.Member):
        """Aktualisiert den Status einer Bewerbung."""
        msg_id = str(interaction.message.id)
        async with self.config.guild(interaction.guild).applications() as apps:
            if msg_id in apps:
                apps[msg_id]["status"] = status
                apps[msg_id]["decided_by"] = admin.id

    async def check_perm_interaction(self, interaction: discord.Interaction) -> bool:
        """Prüft, ob der User Bewerbungen bearbeiten darf."""
        if interaction.user.guild_permissions.manage_roles:
            return True

        ping_role_id = await self.config.guild(interaction.guild).ping_role()
        if ping_role_id:
            role = interaction.guild.get_role(ping_role_id)
            if role and role in interaction.user.roles:
                return True

        extra_roles = await self.config.guild(interaction.guild).extra_wl_roles()
        for role_id in extra_roles:
            role = interaction.guild.get_role(role_id)
            if role and role in interaction.user.roles:
                return True

        return False

    async def check_perms(self, ctx_or_interaction) -> bool:
        user = None
        guild = None

        if isinstance(ctx_or_interaction, commands.Context):
            user = ctx_or_interaction.author
            guild = ctx_or_interaction.guild
            if user.guild_permissions.manage_guild:
                return True
        elif isinstance(ctx_or_interaction, discord.Interaction):
            return await self.check_perm_interaction(ctx_or_interaction)
        else:
            return False

        ping_role_id = await self.config.guild(guild).ping_role()
        if ping_role_id and guild.get_role(ping_role_id) in user.roles:
            return True

        extra_roles = await self.config.guild(guild).extra_wl_roles()
        for role_id in extra_roles:
            if guild.get_role(role_id) in user.roles:
                return True

        return False

    # ---------------- COMMANDS ----------------

    @commands.group(name="lwhitelist", invoke_without_command=False)
    @commands.admin_or_permissions(manage_guild=True)
    async def lwhitelist_group(self, ctx: commands.Context):
        """Einstellungen für das FiveM Whitelist System."""
        pass

    @commands.command(name="lw")
    async def manual_add_wl(self, ctx: commands.Context, user_id: int):
        """Fügt einem User manuell die Whitelist-Rolle hinzu."""
        if not await self.check_perms(ctx):
            return await ctx.send("❌ Du hast keine Berechtigung.", delete_after=10)

        wl_role_id = await self.config.guild(ctx.guild).wl_role()
        if not wl_role_id:
            return await ctx.send("❌ Es ist keine Whitelist-Rolle hinterlegt.")

        wl_role = ctx.guild.get_role(wl_role_id)
        if not wl_role:
            return await ctx.send("❌ Die Whitelist-Rolle existiert nicht mehr.")

        try:
            member = await ctx.guild.fetch_member(user_id)
        except discord.NotFound:
            return await ctx.send("❌ User nicht auf diesem Server gefunden.")
        except discord.HTTPException:
            return await ctx.send("❌ Fehler beim Abrufen des Users.")

        if wl_role in member.roles:
            return await ctx.send("ℹ️ Dieser User hat die Whitelist bereits.")

        try:
            await member.add_roles(wl_role)
            await ctx.send(f"✅ {member.mention} hat nun die Whitelist-Rolle {wl_role.mention}.")
        except discord.Forbidden:
            await ctx.send("❌ Keine Berechtigung, diese Rolle zu vergeben.")

    @commands.command(name="luw")
    async def manual_remove_wl(self, ctx: commands.Context, user_id: int):
        """Entfernt einem User manuell die Whitelist-Rolle."""
        if not await self.check_perms(ctx):
            return await ctx.send("❌ Du hast keine Berechtigung.", delete_after=10)

        wl_role_id = await self.config.guild(ctx.guild).wl_role()
        if not wl_role_id:
            return await ctx.send("❌ Es ist keine Whitelist-Rolle hinterlegt.")

        wl_role = ctx.guild.get_role(wl_role_id)
        if not wl_role:
            return await ctx.send("❌ Die Whitelist-Rolle existiert nicht mehr.")

        try:
            member = await ctx.guild.fetch_member(user_id)
        except discord.NotFound:
            return await ctx.send("❌ User nicht gefunden.")

        if wl_role not in member.roles:
            return await ctx.send("ℹ️ Dieser User hat die Whitelist gar nicht.")

        try:
            await member.remove_roles(wl_role)
            await ctx.send(f"✅ Whitelist-Rolle von {member.mention} entfernt.")
        except discord.Forbidden:
            await ctx.send("❌ Keine Berechtigung, diese Rolle zu entfernen.")

    @commands.command(name="lwb")
    async def manual_blacklist(self, ctx: commands.Context, user_id: int, *, reason: str):
        """Setzt einen User auf die Blacklist."""
        if not await self.check_perms(ctx):
            return await ctx.send("❌ Du hast keine Berechtigung.", delete_after=10)

        async with self.config.guild(ctx.guild).blacklist() as blacklist:
            if user_id in blacklist:
                return await ctx.send("ℹ️ User steht bereits auf der Blacklist.")
            blacklist.append(user_id)

        member = None
        try:
            member = await ctx.guild.fetch_member(user_id)
            if member:
                try:
                    await member.send(
                        f"🚫 **Blacklist-Mitteilung**\n\n"
                        f"Du wurdest auf **{ctx.guild.name}** von der Whitelist-Bewerbung ausgeschlossen.\n"
                        f"**Grund:** {reason}"
                    )
                except discord.Forbidden:
                    pass
        except discord.NotFound:
            pass

        # Blacklist-Log
        bl_channel_id = await self.config.guild(ctx.guild).blacklist_channel()
        if bl_channel_id:
            bl_channel = ctx.guild.get_channel(bl_channel_id)
            if bl_channel:
                if V2_AVAILABLE:
                    view = LayoutView()
                    container = Container(accent_color=discord.Color.dark_red())
                    container.add_item(TextDisplay("## 🚫 User geblacklistet"))
                    container.add_item(Separator())
                    user_display = member.mention if member else f"`{user_id}`"
                    container.add_item(TextDisplay(f"**User:** {user_display} (`{user_id}`)"))
                    container.add_item(TextDisplay(f"**Admin:** {ctx.author.mention}"))
                    container.add_item(TextDisplay(f"**Grund:** {reason}"))
                    view.add_item(container)
                    await bl_channel.send(view=view)
                else:
                    embed = discord.Embed(title="🚫 User geblacklistet", color=discord.Color.dark_red())
                    embed.add_field(name="User", value=f"`{user_id}`", inline=False)
                    embed.add_field(name="Admin", value=ctx.author.mention, inline=False)
                    embed.add_field(name="Grund", value=reason, inline=False)
                    await bl_channel.send(embed=embed)

        await ctx.send(f"✅ User `{user_id}` auf die Blacklist gesetzt.")

    @commands.command(name="lunwb")
    async def manual_unblacklist(self, ctx: commands.Context, user_id: int):
        """Entfernt einen User von der Blacklist."""
        if not await self.check_perms(ctx):
            return await ctx.send("❌ Keine Berechtigung.", delete_after=10)

        was = False
        async with self.config.guild(ctx.guild).blacklist() as blacklist:
            if user_id in blacklist:
                blacklist.remove(user_id)
                was = True

        if was:
            await ctx.send(f"✅ User `{user_id}` wurde von der Blacklist entfernt.")
        else:
            await ctx.send("ℹ️ User stand nicht auf der Blacklist.")

    # ---------------- SETUP WIZARD ----------------

    @lwhitelist_group.command(name="wizard")
    async def setup_wizard(self, ctx: commands.Context):
        """Startet den interaktiven Setup-Assistenten."""
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("**[1/5]** Bitte mentione den Channel für die Bewerbungen (z.B. `#team-bewerbungen`).")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Timeout. Abgebrochen.")
        if not msg.channel_mentions:
            return await ctx.send("❌ Kein Channel erwähnt.")
        log_channel = msg.channel_mentions[0]
        await self.config.guild(ctx.guild).log_channel.set(log_channel.id)

        await ctx.send(f"✅ Log-Channel: {log_channel.mention}.\n\n**[2/5]** Mentione die Whitelist-Rolle (z.B. `@Whitelist`).")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Timeout.")
        if not msg.role_mentions:
            return await ctx.send("❌ Keine Rolle erwähnt.")
        wl_role = msg.role_mentions[0]
        await self.config.guild(ctx.guild).wl_role.set(wl_role.id)

        await ctx.send(f"✅ WL-Rolle: {wl_role.mention}.\n\n**[3/5]** Welche Rolle soll bei neuen Bewerbungen gepingt werden? (`skip` für keine)")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Timeout.")
        if msg.content.lower() == "skip":
            await self.config.guild(ctx.guild).ping_role.set(None)
            await ctx.send("✅ Keine Ping-Rolle.")
        elif msg.role_mentions:
            await self.config.guild(ctx.guild).ping_role.set(msg.role_mentions[0].id)
            await ctx.send(f"✅ Ping-Rolle: {msg.role_mentions[0].mention}.")
        else:
            await self.config.guild(ctx.guild).ping_role.set(None)
            await ctx.send("❌ Ungültig. Überspringe Ping-Rolle.")

        await ctx.send("**[4/5]** Weitere Rollen, die Bewerbungen bearbeiten dürfen? (`skip` für keine)")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Timeout.")
        if msg.content.lower() == "skip" or not msg.role_mentions:
            await self.config.guild(ctx.guild).extra_wl_roles.set([])
            await ctx.send("✅ Keine Extra-Rollen.")
        else:
            await self.config.guild(ctx.guild).extra_wl_roles.set([r.id for r in msg.role_mentions])
            await ctx.send(f"✅ Extra-Rollen gesetzt.")

        await ctx.send("**[5/5]** Channel für Blacklist-Einträge? (`skip` für keinen)")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Timeout.")
        if msg.content.lower() == "skip" or not msg.channel_mentions:
            await self.config.guild(ctx.guild).blacklist_channel.set(None)
            await ctx.send("✅ Kein Blacklist-Channel.")
        else:
            await self.config.guild(ctx.guild).blacklist_channel.set(msg.channel_mentions[0].id)
            await ctx.send(f"✅ Blacklist-Channel: {msg.channel_mentions[0].mention}.")

        await ctx.send("🎉 **Setup abgeschlossen!** Poste das Panel mit `!lwhitelist setup`.")

    @lwhitelist_group.command(name="setchannel")
    async def set_log_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Setzt den Bewerbungs-Channel."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Bewerbungs-Channel: {channel.mention}.")

    @lwhitelist_group.command(name="setblchannel")
    async def set_bl_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Setzt den Blacklist-Log-Channel."""
        await self.config.guild(ctx.guild).blacklist_channel.set(channel.id if channel else None)
        await ctx.send(f"✅ Blacklist-Channel: {channel.mention}." if channel else "✅ Entfernt.")

    @lwhitelist_group.command(name="setrole")
    async def set_wl_role(self, ctx: commands.Context, role: discord.Role):
        """Setzt die Whitelist-Rolle."""
        await self.config.guild(ctx.guild).wl_role.set(role.id)
        await ctx.send(f"✅ Whitelist-Rolle: {role.mention}.")

    @lwhitelist_group.command(name="setpingrole")
    async def set_ping_role(self, ctx: commands.Context, role: discord.Role = None):
        """Setzt die Ping-Rolle."""
        await self.config.guild(ctx.guild).ping_role.set(role.id if role else None)
        await ctx.send(f"✅ Ping-Rolle: {role.mention}." if role else "✅ Entfernt.")

    @lwhitelist_group.command(name="addwlrole")
    async def add_wl_role(self, ctx: commands.Context, role: discord.Role):
        """Fügt eine Rolle hinzu, die Bewerbungen bearbeiten darf."""
        async with self.config.guild(ctx.guild).extra_wl_roles() as extra:
            if role.id not in extra:
                extra.append(role.id)
        await ctx.send(f"✅ {role.mention} kann nun Bewerbungen bearbeiten.")

    @lwhitelist_group.command(name="removewlrole")
    async def remove_wl_role(self, ctx: commands.Context, role: discord.Role):
        """Entfernt eine Extra-Whitelister-Rolle."""
        async with self.config.guild(ctx.guild).extra_wl_roles() as extra:
            if role.id in extra:
                extra.remove(role.id)
        await ctx.send(f"✅ {role.mention} entfernt.")

    @lwhitelist_group.command(name="settings")
    async def show_settings(self, ctx: commands.Context):
        """Zeigt die aktuellen Einstellungen an."""
        settings = await self.config.guild(ctx.guild).all()

        log_ch = ctx.guild.get_channel(settings["log_channel"]) if settings["log_channel"] else None
        wl_r = ctx.guild.get_role(settings["wl_role"]) if settings["wl_role"] else None
        ping_r = ctx.guild.get_role(settings["ping_role"]) if settings["ping_role"] else None
        bl_ch = ctx.guild.get_channel(settings["blacklist_channel"]) if settings["blacklist_channel"] else None
        extra_rs = [ctx.guild.get_role(r) for r in settings["extra_wl_roles"]]
        extra_rs = [r for r in extra_rs if r]
        extra_str = ", ".join([r.mention for r in extra_rs]) if extra_rs else "Keine"
        pending = sum(1 for a in settings["applications"].values() if a.get("status") == "pending")

        if V2_AVAILABLE:
            view = LayoutView()
            container = Container(accent_color=discord.Color.dark_blue())
            container.add_item(TextDisplay("## ⚙️ Whitelist System Einstellungen"))
            container.add_item(Separator())
            container.add_item(TextDisplay(f"**Bewerbungs-Channel:** {log_ch.mention if log_ch else 'Nicht gesetzt'}"))
            container.add_item(TextDisplay(f"**Whitelist-Rolle:** {wl_r.mention if wl_r else 'Nicht gesetzt'}"))
            container.add_item(TextDisplay(f"**Ping-Rolle:** {ping_r.mention if ping_r else 'Nicht gesetzt'}"))
            container.add_item(TextDisplay(f"**Extra Whitelister:** {extra_str}"))
            container.add_item(TextDisplay(f"**Blacklist-Channel:** {bl_ch.mention if bl_ch else 'Nicht gesetzt'}"))
            container.add_item(TextDisplay(f"**Offene Bewerbungen:** {pending}"))
            view.add_item(container)
            await ctx.send(view=view)
        else:
            embed = discord.Embed(title="⚙️ Whitelist Einstellungen", color=discord.Color.dark_blue())
            embed.add_field(name="Bewerbungs-Channel", value=log_ch.mention if log_ch else "Nicht gesetzt", inline=False)
            embed.add_field(name="Whitelist-Rolle", value=wl_r.mention if wl_r else "Nicht gesetzt", inline=False)
            embed.add_field(name="Ping-Rolle", value=ping_r.mention if ping_r else "Nicht gesetzt", inline=False)
            embed.add_field(name="Extra Whitelister", value=extra_str, inline=False)
            embed.add_field(name="Blacklist-Channel", value=bl_ch.mention if bl_ch else "Nicht gesetzt", inline=False)
            await ctx.send(embed=embed)

    @lwhitelist_group.command(name="setup")
    async def setup_panel(self, ctx: commands.Context):
        """Sendet das Panel, auf das User klicken können."""
        if not V2_AVAILABLE:
            return await ctx.send("❌ Deine discord.py Version unterstützt keine Components V2.")

        view = WhitelistButtonView(self.config)
        await ctx.send(view=view)

    @lwhitelist_group.command(name="stats")
    async def stats(self, ctx: commands.Context):
        """Zeigt eine Statistik über alle Bewerbungen."""
        apps = await self.config.guild(ctx.guild).applications()
        pending = sum(1 for a in apps.values() if a.get("status") == "pending")
        accepted = sum(1 for a in apps.values() if a.get("status") == "accepted")
        rejected = sum(1 for a in apps.values() if a.get("status") == "rejected")
        questions = sum(1 for a in apps.values() if a.get("status") == "questions")
        blacklist = len(await self.config.guild(ctx.guild).blacklist())

        if V2_AVAILABLE:
            view = LayoutView()
            container = Container(accent_color=discord.Color.dark_blue())
            container.add_item(TextDisplay("## 📊 Whitelist-Statistik"))
            container.add_item(Separator())
            container.add_item(TextDisplay(f"⏳ **Offen:** {pending}"))
            container.add_item(TextDisplay(f"✅ **Angenommen:** {accepted}"))
            container.add_item(TextDisplay(f"❌ **Abgelehnt:** {rejected}"))
            container.add_item(TextDisplay(f"❓ **Rückfragen:** {questions}"))
            container.add_item(TextDisplay(f"🚫 **Blacklist:** {blacklist}"))
            view.add_item(container)
            await ctx.send(view=view)
        else:
            embed = discord.Embed(title="📊 Whitelist-Statistik", color=discord.Color.dark_blue())
            embed.add_field(name="Offen", value=pending)
            embed.add_field(name="Angenommen", value=accepted)
            embed.add_field(name="Abgelehnt", value=rejected)
            embed.add_field(name="Rückfragen", value=questions)
            embed.add_field(name="Blacklist", value=blacklist)
            await ctx.send(embed=embed)


async def setup(bot: Red):
    await bot.add_cog(FiveMWhitelist(bot))
