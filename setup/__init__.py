"""
Cog: Vollständiger Setup-Assistent (mit automatischem Flag-Reset)
- Fortschrittsbalken
- Setup speichern & fortsetzen
- Live-Vorschau
- Springen zwischen Schritten
- Deutsch / Englisch
- Export & Import
- Behebt "A setup is already running" nach Timeout/Neustart
"""
import discord
from redbot.core import commands, Config
from discord.ui import View, Button, Select, Modal, TextInput
from typing import Optional, Dict, Any, List
import asyncio
import json
import io

# -------------------------------------------------------------------
# Mehrsprachige Textbausteine
# -------------------------------------------------------------------
TEXTS = {
    "en-US": {
        "start_title": "🛠️ Setup Wizard",
        "start_desc": "This wizard helps you configure the most important bot settings for this server.",
        "start_steps": "The following areas will be configured:\n1. **General** – Prefix, Language, Color\n2. **Roles** – Admin, Moderator, Mute\n3. **Logs** – Mod-Log, Server-Log, Message-Log\n4. **Moderation** – DM on punishments, Auto-Mod\n5. **Final** – Additional options & save",
        "start_footer": "Requested by {user}",
        "start_button": "Start",
        "resume_title": "Saved Setup Found",
        "resume_desc": "A previously interrupted setup was detected for this server.\nWhat would you like to do?",
        "resume_resume": "Resume",
        "resume_new": "Start Over",
        "general_title": "Step 1: General Settings",
        "general_prefix": "Prefix",
        "general_locale": "Language",
        "general_color": "Embed Color",
        "general_format": "Date Format",
        "general_current_prefix": "Current Prefix: `{prefix}`",
        "general_current_locale": "Language: {locale}",
        "general_current_format": "Date Format: {format}",
        "general_current_color": "Embed Color: {color}",
        "general_example": "Example: `{prefix}help`",
        "roles_title": "Step 2: Roles",
        "roles_admin": "Admin Role",
        "roles_mod": "Moderator Role",
        "roles_mute": "Mute Role",
        "roles_none": "None",
        "logs_title": "Step 3: Logging Channels",
        "logs_modlog": "Mod-Log",
        "logs_serverlog": "Server-Log",
        "logs_messagelog": "Message-Log",
        "mod_title": "Step 4: Moderation",
        "mod_dm_kick": "DM on Kick",
        "mod_dm_ban": "DM on Ban",
        "mod_auto": "Auto-Mod Settings",
        "final_title": "Step 5: Final & Save",
        "final_embeds": "Disable Embeds",
        "final_save": "Save & Finish",
        "saved_title": "✅ Setup Complete",
        "saved_desc": "All settings have been saved successfully.",
        "export_title": "Config exported",
        "export_sent": "Your server configuration has been exported as JSON.",
        "import_success": "Configuration imported successfully.",
        "import_fail": "Invalid JSON or missing data.",
        "progress_bar": "Progress",
        "jump_to": "Jump to step...",
        "step_names": ["General", "Roles", "Logging", "Moderation", "Final"],
        "color_modal_title": "Set Embed Color",
        "color_label": "Color (Hex Code)",
        "color_placeholder": "#ff0000 or ff0000",
        "prefix_modal_title": "Set Prefix",
        "prefix_label": "Prefix",
        "prefix_placeholder": "e.g. ? or $",
        "automod_modal_title": "Auto-Mod Settings",
        "automod_mentions": "Max Mentions",
        "automod_role_mentions": "Max Role Mentions",
        "automod_spam": "Spam Detection (sec)",
        "on": "ON",
        "off": "OFF",
        "back": "Back",
        "next": "Next",
        "set": "Set",
    },
    "de": {
        "start_title": "🛠️ Einrichtungsassistent",
        "start_desc": "Dieser Assistent hilft dir, die wichtigsten Bot-Einstellungen für diesen Server vorzunehmen.",
        "start_steps": "Folgende Bereiche werden konfiguriert:\n1. **Allgemein** – Prefix, Sprache, Farbe\n2. **Rollen** – Admin, Moderator, Mute\n3. **Logs** – Mod-Log, Server-Log, Nachrichten-Log\n4. **Moderation** – DM bei Bestrafungen, Auto-Mod\n5. **Abschluss** – Weitere Optionen & Speichern",
        "start_footer": "Angefordert von {user}",
        "start_button": "Starten",
        "resume_title": "Gespeichertes Setup gefunden",
        "resume_desc": "Für diesen Server wurde ein unterbrochenes Setup gefunden.\nWas möchtest du tun?",
        "resume_resume": "Fortsetzen",
        "resume_new": "Neu starten",
        "general_title": "Schritt 1: Allgemeine Einstellungen",
        "general_prefix": "Prefix",
        "general_locale": "Sprache",
        "general_color": "Embed-Farbe",
        "general_format": "Datumsformat",
        "general_current_prefix": "Aktueller Prefix: `{prefix}`",
        "general_current_locale": "Sprache: {locale}",
        "general_current_format": "Datumsformat: {format}",
        "general_current_color": "Embed-Farbe: {color}",
        "general_example": "Beispiel: `{prefix}help`",
        "roles_title": "Schritt 2: Rollen",
        "roles_admin": "Admin-Rolle",
        "roles_mod": "Moderator-Rolle",
        "roles_mute": "Mute-Rolle",
        "roles_none": "Keine",
        "logs_title": "Schritt 3: Logging-Kanäle",
        "logs_modlog": "Mod-Log",
        "logs_serverlog": "Server-Log",
        "logs_messagelog": "Nachrichten-Log",
        "mod_title": "Schritt 4: Moderation",
        "mod_dm_kick": "DM bei Kick",
        "mod_dm_ban": "DM bei Ban",
        "mod_auto": "Auto-Mod Einstellungen",
        "final_title": "Schritt 5: Abschluss & Speichern",
        "final_embeds": "Embeds deaktivieren",
        "final_save": "Speichern & Fertig",
        "saved_title": "✅ Setup abgeschlossen",
        "saved_desc": "Alle Einstellungen wurden erfolgreich gespeichert.",
        "export_title": "Konfiguration exportiert",
        "export_sent": "Deine Serverkonfiguration wurde als JSON exportiert.",
        "import_success": "Konfiguration erfolgreich importiert.",
        "import_fail": "Ungültiges JSON oder fehlende Daten.",
        "progress_bar": "Fortschritt",
        "jump_to": "Springe zu Schritt...",
        "step_names": ["Allgemein", "Rollen", "Logs", "Moderation", "Abschluss"],
        "color_modal_title": "Embed-Farbe festlegen",
        "color_label": "Farbe (Hex-Code)",
        "color_placeholder": "#ff0000 oder ff0000",
        "prefix_modal_title": "Prefix festlegen",
        "prefix_label": "Prefix",
        "prefix_placeholder": "z.B. ? oder $",
        "automod_modal_title": "Auto-Mod Einstellungen",
        "automod_mentions": "Maximale Erwähnungen",
        "automod_role_mentions": "Max. Rollen-Erwähnungen",
        "automod_spam": "Spam-Erkennung (Sek.)",
        "on": "AN",
        "off": "AUS",
        "back": "Zurück",
        "next": "Weiter",
        "set": "Ändern",
    },
}

# -------------------------------------------------------------------
# Haupt-Cog
# -------------------------------------------------------------------
class SetupWizardCog(commands.Cog):
    """Erweiterter interaktiver Einrichtungsassistent."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1642872399, force_registration=True)
        default_guild = {
            "setup_in_progress": False,
            "saved_setup": None,
            "setup_message_id": None,
            "setup_channel_id": None,
        }
        self.config.register_guild(**default_guild)

    @staticmethod
    def t(locale: str, key: str, **kwargs) -> str:
        texts = TEXTS.get(locale, TEXTS["en-US"])
        return texts.get(key, TEXTS["en-US"][key]).format(**kwargs)

    @commands.command(name="setup")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setup(self, ctx: commands.Context):
        """Startet den interaktiven Einrichtungsassistenten."""
        try:
            # Prüfen, ob ein Setup läuft – wenn ja, checken wir ob die alte Nachricht noch da ist
            if await self.config.guild(ctx.guild).setup_in_progress():
                msg_id = await self.config.guild(ctx.guild).setup_message_id()
                ch_id = await self.config.guild(ctx.guild).setup_channel_id()
                if msg_id and ch_id:
                    channel = ctx.guild.get_channel(ch_id)
                    if channel:
                        try:
                            msg = await channel.fetch_message(msg_id)
                            # Nachricht existiert noch, also wirklich aktiv
                            await ctx.send("A setup is already running on this server. Please complete or cancel it first.")
                            return
                        except (discord.NotFound, discord.Forbidden):
                            pass  # Nachricht gelöscht oder kein Zugriff -> Flag löschen
                # Alte Nachricht existiert nicht mehr – Flag zurücksetzen
                await self.config.guild(ctx.guild).setup_in_progress.set(False)
                await self.config.guild(ctx.guild).setup_message_id.clear()
                await self.config.guild(ctx.guild).setup_channel_id.clear()

            saved = await self.config.guild(ctx.guild).saved_setup()
            if saved:
                await self.config.guild(ctx.guild).setup_in_progress.set(True)
                embed = discord.Embed(
                    title=self.t(saved.get("data", {}).get("locale", "en-US"), "resume_title"),
                    description=self.t(saved.get("data", {}).get("locale", "en-US"), "resume_desc"),
                    color=discord.Color.blue(),
                )
                view = ResumeView(self, ctx.author, ctx.guild, saved)
                message = await ctx.send(embed=embed, view=view)
                view.message = message
                # Neue Message-ID und Channel-ID speichern
                await self.config.guild(ctx.guild).setup_message_id.set(message.id)
                await self.config.guild(ctx.guild).setup_channel_id.set(message.channel.id)
                return

            await self.start_fresh_setup(ctx)
        except Exception as e:
            await ctx.send(f"❌ Error in setup: {e}")

    @commands.command(name="setupforce")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setup_force(self, ctx: commands.Context):
        """Erzwingt einen Neustart des Wizards (löscht den aktuellen Status)."""
        await self.config.guild(ctx.guild).setup_in_progress.set(False)
        await self.config.guild(ctx.guild).saved_setup.clear()
        await self.config.guild(ctx.guild).setup_message_id.clear()
        await self.config.guild(ctx.guild).setup_channel_id.clear()
        await ctx.send("Setup-Status wurde zurückgesetzt. Starte Wizard...")
        await self.start_fresh_setup(ctx)

    async def start_fresh_setup(self, source):
        if isinstance(source, commands.Context):
            guild = source.guild
            author = source.author
            send = source.send
        else:
            guild = source.guild
            author = source.user
            send = source.channel.send

        await self.config.guild(guild).setup_in_progress.set(True)
        data = await DataCollector(self, guild).collect_all()
        locale = data.get("locale", "en-US")

        embed = discord.Embed(
            title=self.t(locale, "start_title"),
            description=f"{self.t(locale, 'start_desc')}\n\n{self.t(locale, 'start_steps')}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=self.t(locale, "start_footer", user=author.display_name))
        view = StartView(self, author, guild, data)
        message = await send(embed=embed, view=view)
        view.message = message
        # Message-ID und Channel-ID speichern
        await self.config.guild(guild).setup_message_id.set(message.id)
        await self.config.guild(guild).setup_channel_id.set(message.channel.id)

    @commands.command(name="exportsetup")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def export_setup(self, ctx: commands.Context):
        try:
            data = await DataCollector(self, ctx.guild).collect_all()
            for key in ["embed_color", "use_bot_color"]:
                if isinstance(data.get(key), discord.Color):
                    data[key] = data[key].value
            json_str = json.dumps(data, indent=4, default=str)
            file = discord.File(io.StringIO(json_str), filename="server_config.json")
            await ctx.send(self.t(data.get("locale", "en-US"), "export_sent"), file=file)
        except Exception as e:
            await ctx.send(f"❌ Export failed: {e}")

    @commands.command(name="importsetup")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def import_setup(self, ctx: commands.Context):
        if not ctx.message.attachments:
            return await ctx.send("Please attach a .json file.")
        attachment = ctx.message.attachments[0]
        try:
            content = await attachment.read()
            data = json.loads(content)
        except Exception:
            return await ctx.send(self.t("en-US", "import_fail"))
        if not isinstance(data, dict):
            return await ctx.send(self.t("en-US", "import_fail"))
        try:
            await self._apply_imported_settings(ctx.guild, data)
            await ctx.send(self.t(data.get("locale", "en-US"), "import_success"))
        except Exception as e:
            await ctx.send(f"❌ Import failed: {e}")

    async def _apply_imported_settings(self, guild: discord.Guild, data: dict):
        core_conf = self.bot.core_config
        if core_conf:
            await core_conf.guild(guild).prefix.set(data.get("prefix", "!"))
            await core_conf.guild(guild).locale.set(data.get("locale", "en-US"))
            await core_conf.guild(guild).regional_format.set(data.get("regional_format", "en-US"))
            await core_conf.guild(guild).use_bot_color.set(data.get("use_bot_color", False))
            await core_conf.guild(guild).embeds_disabled.set(data.get("embeds_disabled", False))
            await core_conf.guild(guild).admin_role.set(data.get("admin_role"))
            await core_conf.guild(guild).mod_role.set(data.get("mod_role"))
            if "embed_color" in data and isinstance(data["embed_color"], int):
                await core_conf.guild(guild).embed_color.set(data["embed_color"])
        mod_cog = self.bot.get_cog("Mod")
        if mod_cog:
            await mod_cog.config.guild(guild).modlog_channel.set(data.get("modlog_channel"))
            await mod_cog.config.guild(guild).mute_role.set(data.get("mute_role"))
            await mod_cog.config.guild(guild).dm_on_kick.set(data.get("dm_on_kick", False))
            await mod_cog.config.guild(guild).dm_on_ban.set(data.get("dm_on_ban", False))
            if data.get("auto_mod"):
                await mod_cog.config.guild(guild).auto_mod.set(data["auto_mod"])
        logs_cog = self.bot.get_cog("Logs")
        if logs_cog:
            await logs_cog.config.guild(guild).serverlog_channel.set(data.get("serverlog_channel"))
            await logs_cog.config.guild(guild).messagelog_channel.set(data.get("messagelog_channel"))

# -------------------------------------------------------------------
# Daten-Helfer
# -------------------------------------------------------------------
class DataCollector:
    def __init__(self, cog: SetupWizardCog, guild: discord.Guild):
        self.cog = cog
        self.bot = cog.bot
        self.guild = guild

    async def collect_all(self) -> Dict[str, Any]:
        data = {
            "prefix": None,
            "locale": None,
            "regional_format": None,
            "use_bot_color": None,
            "embed_color": None,
            "embeds_disabled": False,
            "admin_role": None,
            "mod_role": None,
            "mute_role": None,
            "modlog_channel": None,
            "serverlog_channel": None,
            "messagelog_channel": None,
            "dm_on_kick": False,
            "dm_on_ban": False,
            "auto_mod": {},
        }
        core_conf = getattr(self.bot, "core_config", None)
        if core_conf:
            data["prefix"] = await core_conf.guild(self.guild).prefix()
            data["locale"] = await core_conf.guild(self.guild).locale()
            data["regional_format"] = await core_conf.guild(self.guild).regional_format()
            data["use_bot_color"] = await core_conf.guild(self.guild).use_bot_color()
            data["embeds_disabled"] = await core_conf.guild(self.guild).embeds_disabled()
            data["admin_role"] = await core_conf.guild(self.guild).admin_role()
            data["mod_role"] = await core_conf.guild(self.guild).mod_role()
            try:
                color = await core_conf.guild(self.guild).embed_color()
                data["embed_color"] = color.value if isinstance(color, discord.Color) else color
            except:
                data["embed_color"] = None
        mod_cog = self.bot.get_cog("Mod")
        if mod_cog:
            data["modlog_channel"] = await mod_cog.config.guild(self.guild).modlog_channel()
            data["mute_role"] = await mod_cog.config.guild(self.guild).mute_role()
            data["dm_on_kick"] = await mod_cog.config.guild(self.guild).dm_on_kick()
            data["dm_on_ban"] = await mod_cog.config.guild(self.guild).dm_on_ban()
            data["auto_mod"] = await mod_cog.config.guild(self.guild).auto_mod()
        logs_cog = self.bot.get_cog("Logs")
        if logs_cog:
            data["serverlog_channel"] = await logs_cog.config.guild(self.guild).serverlog_channel()
            data["messagelog_channel"] = await logs_cog.config.guild(self.guild).messagelog_channel()
        return data

# -------------------------------------------------------------------
# Basis-View
# -------------------------------------------------------------------
class BaseStepView(View):
    step_number = 1
    total_steps = 5

    def __init__(self, cog: SetupWizardCog, author: discord.Member, guild: discord.Guild, data: Dict[str, Any], message: discord.Message):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.guild = guild
        self.data = data
        self.message = message
        self.locale = data.get("locale", "en-US")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            return False
        return True

    def t(self, key: str, **kwargs) -> str:
        return SetupWizardCog.t(self.locale, key, **kwargs)

    def progress_bar(self) -> str:
        filled = int(self.step_number / self.total_steps * 10)
        empty = 10 - filled
        return f"[{'█'*filled}{'░'*empty}] {self.step_number}/{self.total_steps}"

    def build_embed(self) -> discord.Embed:
        raise NotImplementedError

    async def go_to_next(self, interaction: discord.Interaction, next_view_class):
        next_view = next_view_class(self.cog, self.author, self.guild, self.data, self.message)
        embed = next_view.build_embed()
        await interaction.response.edit_message(embed=embed, view=next_view)

    async def go_to_previous(self, interaction: discord.Interaction, previous_view_class):
        prev_view = previous_view_class(self.cog, self.author, self.guild, self.data, self.message)
        embed = prev_view.build_embed()
        await interaction.response.edit_message(embed=embed, view=prev_view)

    async def jump_to_step(self, interaction: discord.Interaction, step_index: int):
        step_map = {1: Step1GeneralView, 2: Step2RolesView, 3: Step3LogsView, 4: Step4ModerationView, 5: Step5FinalView}
        view_class = step_map.get(step_index)
        if view_class:
            new_view = view_class(self.cog, self.author, self.guild, self.data, self.message)
            embed = new_view.build_embed()
            await interaction.response.edit_message(embed=embed, view=new_view)

    async def on_timeout(self):
        await self.cog.config.guild(self.guild).saved_setup.set({
            "data": self.data,
            "step": self.step_number,
        })
        await self.cog.config.guild(self.guild).setup_in_progress.set(False)
        await self.cog.config.guild(self.guild).setup_message_id.clear()
        await self.cog.config.guild(self.guild).setup_channel_id.clear()
        try:
            await self.message.edit(view=None)
        except discord.NotFound:
            pass

# -------------------------------------------------------------------
# Resume-View
# -------------------------------------------------------------------
class ResumeView(View):
    def __init__(self, cog: SetupWizardCog, author: discord.Member, guild: discord.Guild, saved: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.guild = guild
        self.saved = saved
        self.message: Optional[discord.Message] = None
        self.locale = saved["data"].get("locale", "en-US")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            return False
        return True

    def t(self, key, **kwargs):
        return SetupWizardCog.t(self.locale, key, **kwargs)

    @discord.ui.button(label="Fortsetzen", style=discord.ButtonStyle.green)
    async def resume(self, interaction: discord.Interaction, button: Button):
        await self.cog.config.guild(self.guild).saved_setup.clear()
        data = self.saved["data"]
        step = self.saved["step"]
        step_map = {1: Step1GeneralView, 2: Step2RolesView, 3: Step3LogsView, 4: Step4ModerationView, 5: Step5FinalView}
        view_class = step_map.get(step, Step1GeneralView)
        view = view_class(self.cog, self.author, self.guild, data, self.message)
        embed = view.build_embed()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Neu starten", style=discord.ButtonStyle.red)
    async def restart(self, interaction: discord.Interaction, button: Button):
        await self.cog.config.guild(self.guild).saved_setup.clear()
        await self.cog.start_fresh_setup(interaction)

# -------------------------------------------------------------------
# Start-View
# -------------------------------------------------------------------
class StartView(View):
    def __init__(self, cog: SetupWizardCog, author: discord.Member, guild: discord.Guild, data: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.guild = guild
        self.message: Optional[discord.Message] = None
        self.data = data

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Start", style=discord.ButtonStyle.green)
    async def start_button(self, interaction: discord.Interaction, button: Button):
        next_view = Step1GeneralView(self.cog, self.author, self.guild, self.data, self.message)
        embed = next_view.build_embed()
        await interaction.response.edit_message(embed=embed, view=next_view)

    async def on_timeout(self):
        await self.cog.config.guild(self.guild).setup_in_progress.set(False)
        await self.cog.config.guild(self.guild).setup_message_id.clear()
        await self.cog.config.guild(self.guild).setup_channel_id.clear()

# -------------------------------------------------------------------
# Schritt 1: Allgemein
# -------------------------------------------------------------------
class Step1GeneralView(BaseStepView):
    step_number = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        btn_prefix = Button(label=self.t("general_prefix"), style=discord.ButtonStyle.primary)
        btn_prefix.callback = self.prefix_callback
        self.add_item(btn_prefix)

        sel_locale = Select(
            placeholder="Sprache / Language",
            options=[
                discord.SelectOption(label="Deutsch", value="de"),
                discord.SelectOption(label="English (US)", value="en-US"),
                discord.SelectOption(label="English (UK)", value="en-GB"),
                discord.SelectOption(label="Français", value="fr"),
                discord.SelectOption(label="Español", value="es"),
                discord.SelectOption(label="Italiano", value="it"),
            ]
        )
        sel_locale.callback = self.locale_callback
        self.add_item(sel_locale)

        btn_color = Button(label=self.t("general_color"), style=discord.ButtonStyle.primary)
        btn_color.callback = self.color_callback
        self.add_item(btn_color)

        sel_format = Select(
            placeholder="Datumsformat",
            options=[
                discord.SelectOption(label="Deutschland (TT.MM.JJJJ)", value="de"),
                discord.SelectOption(label="USA (MM/TT/JJJJ)", value="en-US"),
                discord.SelectOption(label="UK (TT/MM/JJJJ)", value="en-GB"),
                discord.SelectOption(label="Frankreich (JJJJ-MM-TT)", value="fr"),
            ]
        )
        sel_format.callback = self.regional_callback
        self.add_item(sel_format)

        jump_options = [discord.SelectOption(label=f"{i}. {name}", value=str(i)) for i, name in enumerate(self.t("step_names").split(", "), 1)]
        sel_jump = Select(placeholder=self.t("jump_to"), options=jump_options)
        sel_jump.callback = self.jump_callback
        self.add_item(sel_jump)

        btn_next = Button(label=self.t("next"), style=discord.ButtonStyle.green, row=2)
        btn_next.callback = self.next_step
        self.add_item(btn_next)

    async def prefix_callback(self, interaction: discord.Interaction):
        modal = PrefixModal(self)
        await interaction.response.send_modal(modal)

    async def locale_callback(self, interaction: discord.Interaction):
        self.locale = interaction.data["values"][0]
        self.data["locale"] = self.locale
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def color_callback(self, interaction: discord.Interaction):
        modal = ColorModal(self)
        await interaction.response.send_modal(modal)

    async def regional_callback(self, interaction: discord.Interaction):
        self.data["regional_format"] = interaction.data["values"][0]
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def jump_callback(self, interaction: discord.Interaction):
        step = int(interaction.data["values"][0])
        await self.jump_to_step(interaction, step)

    async def next_step(self, interaction: discord.Interaction):
        await self.go_to_next(interaction, Step2RolesView)

    def build_embed(self) -> discord.Embed:
        prefix = self.data.get("prefix", "!")
        color_hex = f"#{self.data.get('embed_color'):06x}" if isinstance(self.data.get("embed_color"), int) else "Default"
        locale_name = {"de": "Deutsch", "en-US": "English (US)", "en-GB": "English (UK)", "fr": "Français", "es": "Español", "it": "Italiano"}.get(self.data.get("locale"), "?")
        format_name = {"de": "TT.MM.JJJJ", "en-US": "MM/TT/JJJJ", "en-GB": "TT/MM/JJJJ", "fr": "JJJJ-MM-TT"}.get(self.data.get("regional_format"), "?")
        embed = discord.Embed(
            title=self.t("general_title"),
            description=f"{self.t('general_current_prefix', prefix=prefix)}\n{self.t('general_current_locale', locale=locale_name)}\n{self.t('general_current_format', format=format_name)}\n{self.t('general_current_color', color=color_hex)}\n\n{self.t('general_example', prefix=prefix)}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{self.t('progress_bar')} {self.progress_bar()}")
        return embed

class PrefixModal(Modal, title="Prefix setzen"):
    prefix_input = TextInput(label="Prefix", placeholder="z.B. ? oder $", required=True, max_length=10)
    def __init__(self, view):
        super().__init__()
        self.view = view
    async def on_submit(self, interaction: discord.Interaction):
        self.view.data["prefix"] = self.prefix_input.value.strip()
        await interaction.response.send_message("✅ Prefix updated.", ephemeral=True)
        await self.view.message.edit(embed=self.view.build_embed(), view=self.view)

class ColorModal(Modal, title="Embed-Farbe festlegen"):
    color_input = TextInput(label="Farbe (Hex)", placeholder="#ff0000", required=False, max_length=7)
    def __init__(self, view):
        super().__init__()
        self.view = view
    async def on_submit(self, interaction: discord.Interaction):
        raw = self.color_input.value.strip()
        if not raw:
            self.view.data["use_bot_color"] = False
            self.view.data["embed_color"] = None
        else:
            raw = raw.lstrip("#")
            try:
                self.view.data["embed_color"] = int(raw, 16)
                self.view.data["use_bot_color"] = True
            except ValueError:
                await interaction.response.send_message("❌ Invalid hex.", ephemeral=True)
                return
        await interaction.response.send_message("✅ Color updated.", ephemeral=True)
        await self.view.message.edit(embed=self.view.build_embed(), view=self.view)

# -------------------------------------------------------------------
# Schritt 2: Rollen
# -------------------------------------------------------------------
class Step2RolesView(BaseStepView):
    step_number = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sel_admin = Select(placeholder="Admin-Rolle", options=self._role_options())
        sel_admin.callback = self.admin_select
        self.add_item(sel_admin)
        sel_mod = Select(placeholder="Mod-Rolle", options=self._role_options())
        sel_mod.callback = self.mod_select
        self.add_item(sel_mod)
        if "mute_role" in self.data:
            sel_mute = Select(placeholder="Mute-Rolle", options=self._role_options())
            sel_mute.callback = self.mute_select
            self.add_item(sel_mute)
        jump_opts = [discord.SelectOption(label=f"{i}. {name}", value=str(i)) for i, name in enumerate(self.t("step_names").split(", "), 1)]
        sel_jump = Select(placeholder=self.t("jump_to"), options=jump_opts)
        sel_jump.callback = self.jump_callback
        self.add_item(sel_jump)
        btn_back = Button(label=self.t("back"), style=discord.ButtonStyle.grey, row=1)
        btn_back.callback = self.prev_step
        self.add_item(btn_back)
        btn_next = Button(label=self.t("next"), style=discord.ButtonStyle.green, row=1)
        btn_next.callback = self.next_step
        self.add_item(btn_next)

    def _role_options(self):
        opts = [discord.SelectOption(label=self.t("roles_none"), value="none")]
        for role in sorted(self.guild.roles, key=lambda r: r.position, reverse=True)[:24]:
            if not role.is_default():
                opts.append(discord.SelectOption(label=role.name, value=str(role.id)))
        return opts

    async def admin_select(self, interaction): await self._role_select(interaction, "admin_role")
    async def mod_select(self, interaction): await self._role_select(interaction, "mod_role")
    async def mute_select(self, interaction): await self._role_select(interaction, "mute_role")
    async def _role_select(self, interaction, key):
        val = interaction.data["values"][0]
        self.data[key] = None if val == "none" else int(val)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def jump_callback(self, interaction):
        await self.jump_to_step(interaction, int(interaction.data["values"][0]))
    async def prev_step(self, interaction, button):
        await self.go_to_previous(interaction, Step1GeneralView)
    async def next_step(self, interaction, button):
        await self.go_to_next(interaction, Step3LogsView)

    def build_embed(self) -> discord.Embed:
        admin_str = self._role_mention(self.data.get("admin_role"))
        mod_str = self._role_mention(self.data.get("mod_role"))
        mute_str = self._role_mention(self.data.get("mute_role")) if "mute_role" in self.data else "–"
        embed = discord.Embed(
            title=self.t("roles_title"),
            description=f"**{self.t('roles_admin')}:** {admin_str}\n**{self.t('roles_mod')}:** {mod_str}\n**{self.t('roles_mute')}:** {mute_str}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{self.t('progress_bar')} {self.progress_bar()}")
        return embed

    def _role_mention(self, rid):
        if not rid: return self.t("roles_none")
        role = self.guild.get_role(rid)
        return role.mention if role else "❌"

# -------------------------------------------------------------------
# Schritt 3: Logs
# -------------------------------------------------------------------
class Step3LogsView(BaseStepView):
    step_number = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sel_modlog = Select(placeholder="Mod-Log", options=self._channel_opts())
        sel_modlog.callback = self.modlog_cb
        self.add_item(sel_modlog)
        sel_serverlog = Select(placeholder="Server-Log", options=self._channel_opts())
        sel_serverlog.callback = self.serverlog_cb
        self.add_item(sel_serverlog)
        sel_messagelog = Select(placeholder="Nachrichten-Log", options=self._channel_opts())
        sel_messagelog.callback = self.messagelog_cb
        self.add_item(sel_messagelog)
        jump_opts = [discord.SelectOption(label=f"{i}. {name}", value=str(i)) for i, name in enumerate(self.t("step_names").split(", "), 1)]
        sel_jump = Select(placeholder=self.t("jump_to"), options=jump_opts)
        sel_jump.callback = self.jump_callback
        self.add_item(sel_jump)
        btn_back = Button(label=self.t("back"), style=discord.ButtonStyle.grey, row=1)
        btn_back.callback = self.prev_step
        self.add_item(btn_back)
        btn_next = Button(label=self.t("next"), style=discord.ButtonStyle.green, row=1)
        btn_next.callback = self.next_step
        self.add_item(btn_next)

    def _channel_opts(self):
        opts = [discord.SelectOption(label=self.t("roles_none"), value="none")]
        for ch in sorted(self.guild.text_channels, key=lambda c: c.position)[:24]:
            if ch.permissions_for(self.guild.me).send_messages:
                opts.append(discord.SelectOption(label=f"#{ch.name}", value=str(ch.id)))
        return opts

    async def modlog_cb(self, interaction): self.data["modlog_channel"] = self._resolve(interaction); await self.update(interaction)
    async def serverlog_cb(self, interaction): self.data["serverlog_channel"] = self._resolve(interaction); await self.update(interaction)
    async def messagelog_cb(self, interaction): self.data["messagelog_channel"] = self._resolve(interaction); await self.update(interaction)
    def _resolve(self, interaction): return None if interaction.data["values"][0] == "none" else int(interaction.data["values"][0])
    async def update(self, interaction): await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def jump_callback(self, interaction): await self.jump_to_step(interaction, int(interaction.data["values"][0]))
    async def prev_step(self, interaction, button): await self.go_to_previous(interaction, Step2RolesView)
    async def next_step(self, interaction, button): await self.go_to_next(interaction, Step4ModerationView)

    def build_embed(self):
        modlog = self._ch_mention(self.data.get("modlog_channel"))
        srvlog = self._ch_mention(self.data.get("serverlog_channel"))
        msglog = self._ch_mention(self.data.get("messagelog_channel"))
        embed = discord.Embed(
            title=self.t("logs_title"),
            description=f"**{self.t('logs_modlog')}:** {modlog}\n**{self.t('logs_serverlog')}:** {srvlog}\n**{self.t('logs_messagelog')}:** {msglog}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{self.t('progress_bar')} {self.progress_bar()}")
        return embed
    def _ch_mention(self, cid):
        if not cid: return self.t("roles_none")
        ch = self.guild.get_channel(cid)
        return ch.mention if ch else "❌"

# -------------------------------------------------------------------
# Schritt 4: Moderation
# -------------------------------------------------------------------
class Step4ModerationView(BaseStepView):
    step_number = 4

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        btn_kick = Button(label=f"{self.t('mod_dm_kick')}: {self.t('on') if self.data.get('dm_on_kick') else self.t('off')}", style=discord.ButtonStyle.secondary)
        btn_kick.callback = self.toggle_kick
        self.add_item(btn_kick)
        btn_ban = Button(label=f"{self.t('mod_dm_ban')}: {self.t('on') if self.data.get('dm_on_ban') else self.t('off')}", style=discord.ButtonStyle.secondary)
        btn_ban.callback = self.toggle_ban
        self.add_item(btn_ban)
        btn_auto = Button(label=self.t("mod_auto"), style=discord.ButtonStyle.primary)
        btn_auto.callback = self.auto_mod_modal
        self.add_item(btn_auto)
        jump_opts = [discord.SelectOption(label=f"{i}. {name}", value=str(i)) for i, name in enumerate(self.t("step_names").split(", "), 1)]
        sel_jump = Select(placeholder=self.t("jump_to"), options=jump_opts)
        sel_jump.callback = self.jump_callback
        self.add_item(sel_jump)
        btn_back = Button(label=self.t("back"), style=discord.ButtonStyle.grey, row=1)
        btn_back.callback = self.prev_step
        self.add_item(btn_back)
        btn_next = Button(label=self.t("next"), style=discord.ButtonStyle.green, row=1)
        btn_next.callback = self.next_step
        self.add_item(btn_next)

    async def toggle_kick(self, interaction):
        self.data["dm_on_kick"] = not self.data.get("dm_on_kick")
        await self.refresh(interaction)
    async def toggle_ban(self, interaction):
        self.data["dm_on_ban"] = not self.data.get("dm_on_ban")
        await self.refresh(interaction)
    async def auto_mod_modal(self, interaction):
        await interaction.response.send_modal(AutoModModal(self))
    async def refresh(self, interaction):
        new_view = Step4ModerationView(self.cog, self.author, self.guild, self.data, self.message)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)
    async def jump_callback(self, interaction): await self.jump_to_step(interaction, int(interaction.data["values"][0]))
    async def prev_step(self, interaction, button): await self.go_to_previous(interaction, Step3LogsView)
    async def next_step(self, interaction, button): await self.go_to_next(interaction, Step5FinalView)

    def build_embed(self):
        return discord.Embed(
            title=self.t("mod_title"),
            description=f"**{self.t('mod_dm_kick')}:** {self.t('on') if self.data.get('dm_on_kick') else self.t('off')}\n**{self.t('mod_dm_ban')}:** {self.t('on') if self.data.get('dm_on_ban') else self.t('off')}\n**{self.t('mod_auto')}:** {'Konfiguriert' if self.data.get('auto_mod') else 'Deaktiviert'}",
            color=discord.Color.blue(),
        ).set_footer(text=f"{self.t('progress_bar')} {self.progress_bar()}")

class AutoModModal(Modal, title="Auto-Mod Einstellungen"):
    max_mentions = TextInput(label="Max Mentions", placeholder="5", required=False)
    max_role_mentions = TextInput(label="Max Role Mentions", placeholder="3", required=False)
    spam_detection = TextInput(label="Spam Detection (sec)", placeholder="2", required=False)
    def __init__(self, view):
        super().__init__()
        self.view = view
    async def on_submit(self, interaction):
        am = self.view.data.get("auto_mod", {})
        try:
            if self.max_mentions.value.strip(): am["max_mentions"] = int(self.max_mentions.value)
            if self.max_role_mentions.value.strip(): am["max_role_mentions"] = int(self.max_role_mentions.value)
            if self.spam_detection.value.strip(): am["spam_detection"] = int(self.spam_detection.value)
            self.view.data["auto_mod"] = am
        except ValueError:
            await interaction.response.send_message("❌ Invalid number.", ephemeral=True)
            return
        await interaction.response.send_message("✅ Auto-Mod saved.", ephemeral=True)
        await self.view.message.edit(embed=self.view.build_embed(), view=self.view)

# -------------------------------------------------------------------
# Schritt 5: Final
# -------------------------------------------------------------------
class Step5FinalView(BaseStepView):
    step_number = 5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        btn_embeds = Button(label=f"{self.t('final_embeds')}: {self.t('on') if self.data.get('embeds_disabled') else self.t('off')}", style=discord.ButtonStyle.secondary)
        btn_embeds.callback = self.toggle_embeds
        self.add_item(btn_embeds)
        jump_opts = [discord.SelectOption(label=f"{i}. {name}", value=str(i)) for i, name in enumerate(self.t("step_names").split(", "), 1)]
        sel_jump = Select(placeholder=self.t("jump_to"), options=jump_opts)
        sel_jump.callback = self.jump_callback
        self.add_item(sel_jump)
        btn_back = Button(label=self.t("back"), style=discord.ButtonStyle.grey, row=1)
        btn_back.callback = self.prev_step
        self.add_item(btn_back)
        btn_save = Button(label=self.t("final_save"), style=discord.ButtonStyle.green, row=1)
        btn_save.callback = self.save
        self.add_item(btn_save)

    async def toggle_embeds(self, interaction):
        self.data["embeds_disabled"] = not self.data.get("embeds_disabled")
        new_view = Step5FinalView(self.cog, self.author, self.guild, self.data, self.message)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)
    async def jump_callback(self, interaction): await self.jump_to_step(interaction, int(interaction.data["values"][0]))
    async def prev_step(self, interaction, button): await self.go_to_previous(interaction, Step4ModerationView)

    async def save(self, interaction, button):
        guild = self.guild
        core_conf = self.cog.bot.core_config
        if core_conf:
            await core_conf.guild(guild).prefix.set(self.data["prefix"])
            await core_conf.guild(guild).locale.set(self.data["locale"])
            await core_conf.guild(guild).regional_format.set(self.data["regional_format"])
            await core_conf.guild(guild).use_bot_color.set(self.data.get("use_bot_color", False))
            await core_conf.guild(guild).embeds_disabled.set(self.data["embeds_disabled"])
            await core_conf.guild(guild).admin_role.set(self.data["admin_role"])
            await core_conf.guild(guild).mod_role.set(self.data["mod_role"])
            if self.data.get("embed_color") is not None:
                await core_conf.guild(guild).embed_color.set(self.data["embed_color"])
        mod_cog = self.cog.bot.get_cog("Mod")
        if mod_cog:
            await mod_cog.config.guild(guild).modlog_channel.set(self.data["modlog_channel"])
            await mod_cog.config.guild(guild).mute_role.set(self.data.get("mute_role"))
            await mod_cog.config.guild(guild).dm_on_kick.set(self.data.get("dm_on_kick", False))
            await mod_cog.config.guild(guild).dm_on_ban.set(self.data.get("dm_on_ban", False))
            if self.data.get("auto_mod"):
                await mod_cog.config.guild(guild).auto_mod.set(self.data["auto_mod"])
        logs_cog = self.cog.bot.get_cog("Logs")
        if logs_cog:
            await logs_cog.config.guild(guild).serverlog_channel.set(self.data["serverlog_channel"])
            await logs_cog.config.guild(guild).messagelog_channel.set(self.data["messagelog_channel"])

        await self.cog.config.guild(guild).saved_setup.clear()
        await self.cog.config.guild(guild).setup_in_progress.set(False)
        await self.cog.config.guild(guild).setup_message_id.clear()
        await self.cog.config.guild(guild).setup_channel_id.clear()
        final_embed = discord.Embed(title=self.t("saved_title"), description=self.t("saved_desc"), color=discord.Color.green())
        self.clear_items()
        await interaction.response.edit_message(embed=final_embed, view=self)

    def build_embed(self):
        return discord.Embed(
            title=self.t("final_title"),
            description=f"**{self.t('final_embeds')}:** {self.t('on') if self.data.get('embeds_disabled') else self.t('off')}",
            color=discord.Color.blue(),
        ).set_footer(text=f"{self.t('progress_bar')} {self.progress_bar()}")

# -------------------------------------------------------------------
# Async Setup
# -------------------------------------------------------------------
async def setup(bot):
    await bot.add_cog(SetupWizardCog(bot))
