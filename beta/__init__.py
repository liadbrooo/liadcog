import discord
import logging
from redbot.core import commands, Config

log = logging.getLogger("red.beta")

try:
    from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Button, Modal, TextInput, View
    V2_AVAILABLE = True
except ImportError:
    V2_AVAILABLE = False


# ==========================================
# MODAL 1
# ==========================================
class BetaModal1(Modal):
    def __init__(self, cog):
        super().__init__(title="Betabewerbung - Teil 1/2")
        self.cog = cog

        self.fivem_discord = TextInput(
            label="FiveM + Discord Name",
            placeholder="z.B. Max Mustermann",
            required=True,
            max_length=100
        )
        self.ic_name = TextInput(
            label="Wie willst du IC heißen?",
            placeholder="z.B. Max Mustermann",
            required=True,
            max_length=100
        )

        self.add_item(self.fivem_discord)
        self.add_item(self.ic_name)

    async def on_submit(self, interaction: discord.Interaction):
        data1 = {
            "fivem_discord": self.fivem_discord.value,
            "ic_name": self.ic_name.value,
            "discord_user_id": interaction.user.id,
            "discord_user_name": str(interaction.user),
            "created_at": discord.utils.utcnow().timestamp(),
        }

        # Zwischenspeichern (persistent über Config)
        async with self.cog.config.guild(interaction.guild).applications() as apps:
            apps[str(interaction.user.id)] = {"data": data1, "status": "pending_modal2"}

        view = View(timeout=600)
        view.add_item(ContinueToModal2Button(interaction.user.id))

        await interaction.response.send_message(
            "✅ **Teil 1 abgeschlossen!**\nKlicke auf den Button unten, um mit **Teil 2** fortzufahren.",
            view=view,
            ephemeral=True
        )


# ==========================================
# MODAL 2
# ==========================================
class BetaModal2(Modal):
    def __init__(self, cog, data1):
        super().__init__(title="Betabewerbung - Teil 2/2")
        self.cog = cog
        self.data1 = data1

        self.fraktion = TextInput(
            label="In welcher Fraktion arbeitest du?",
            placeholder="z.B. Polizeidirektion, Fire Dept, GearHead, Arbeitslos...",
            required=True,
            max_length=100
        )
        self.plan_ic = TextInput(
            label="Was ist dein Plan IC?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.warum_beta = TextInput(
            label="Warum willst du bei uns in die Beta?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.warum_dich = TextInput(
            label="Warum sollten wir dich nehmen?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.bugs = TextInput(
            label="Wirst du Bugs ernst nehmen und melden?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=300
        )

        for item in (self.fraktion, self.plan_ic, self.warum_beta, self.warum_dich, self.bugs):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            **self.data1,
            "fraktion": self.fraktion.value,
            "plan_ic": self.plan_ic.value,
            "warum_beta": self.warum_beta.value,
            "warum_dich": self.warum_dich.value,
            "bugs": self.bugs.value,
        }

        log_message_id = await self.cog.send_application_log(interaction.guild, interaction.user, data)

        async with self.cog.config.guild(interaction.guild).applications() as apps:
            apps[str(interaction.user.id)] = {
                "data": data,
                "status": "pending_review",
                "log_message_id": log_message_id,
            }

        await interaction.response.send_message(
            "✅ **Vielen Dank für deine Bewerbung!**\nSie wurde eingereicht und wird vom Team geprüft.",
            ephemeral=True
        )


# ==========================================
# BUTTONS
# ==========================================
class ContinueToModal2Button(Button):
    def __init__(self, user_id):
        super().__init__(
            label="Weiter zu Teil 2",
            style=discord.ButtonStyle.primary,
            custom_id=f"beta_continue_{user_id}",
            emoji="➡️"
        )


class BetaApplyButton(Button):
    def __init__(self):
        super().__init__(
            label="Jetzt bewerben",
            style=discord.ButtonStyle.primary,
            custom_id="beta_apply_button_v1",
            emoji="📝"
        )

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Beta")
        if not cog:
            return

        # Sperr-Check: Wurde der User abgelehnt?
        rejected = await cog.config.guild(interaction.guild).rejected_users()
        if interaction.user.id in rejected:
            return await interaction.response.send_message(
                "❌ Du wurdest für die Beta abgelehnt und kannst dich nicht erneut bewerben.",
                ephemeral=True
            )

        # Sperr-Check: Läuft schon eine Bewerbung?
        apps = await cog.config.guild(interaction.guild).applications()
        existing = apps.get(str(interaction.user.id))
        if existing:
            status = existing.get("status")
            if status == "accepted":
                return await interaction.response.send_message(
                    "✅ Du bist bereits für die Beta angenommen!",
                    ephemeral=True
                )
            elif status == "rejected":
                return await interaction.response.send_message(
                    "❌ Du wurdest leider abgelehnt.",
                    ephemeral=True
                )
            else:
                return await interaction.response.send_message(
                    "⏳ Du hast bereits eine laufende Bewerbung. Bitte warte auf die Antwort des Teams.",
                    ephemeral=True
                )

        await interaction.response.send_modal(BetaModal1(cog))


# ==========================================
# VIEW
# ==========================================
class BetaApplicationView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = Container(accent_color=discord.Color.dark_blue())
        container.add_item(TextDisplay("## Betabewerbung - Kreis Lindenberg"))
        container.add_item(TextDisplay("Hier kannst du dich auf unserem Server ( Kreis Lindenberg ) für die Beta bewerben."))
        container.add_item(Separator())

        container.add_item(TextDisplay("### Informationen"))
        container.add_item(TextDisplay(
            "1. Es kann sein, dass wir uns dazu entscheiden sollten, doch einen „offenen“ Release durchzuführen und keine Beta zu machen.\n"
            "2. Diese Informationen bleiben Teamintern bzw. High-Team intern!\n"
            "3. Der Release bzw. Beta-Release (falls dieser stattfindet, wird im Discord angekündigt)\n"
            "4. Die Antworten auf die Bewerbungen werden etwas Zeit beanspruchen\n\n"
            "Ansonsten wünschen wir weiterhin viel Spaß und Erfolg :)"
        ))
        container.add_item(Separator())
        container.add_item(TextDisplay("-# Klicke unten auf den Button, um das Bewerbungsformular zu öffnen."))

        row = ActionRow()
        row.add_item(BetaApplyButton())
        container.add_item(row)

        self.add_item(container)


# ==========================================
# HAUPT-COG
# ==========================================
class Beta(commands.Cog):
    """Beta-Bewerbungssystem mit V2 Components, Modals und Annehmen/Ablehnen."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0x42455441)

        default_guild = {
            "channel_id": None,
            "message_id": None,
            "log_channel": None,
            "accepted_role": None,
            "applications": {},   # user_id: {"data": {...}, "status": "...", "log_message_id": int}
            "rejected_users": [], # Liste von User-IDs
        }
        self.config.register_guild(**default_guild)

        if V2_AVAILABLE:
            self.bot.add_view(BetaApplicationView())

    # ---------------- INTERACTION HANDLER ----------------
    # Fängt ALLE persistenten Buttons ab, auch nach Neustart!
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        if not interaction.data or not interaction.guild:
            return

        custom_id = interaction.data.get("custom_id", "")

        if custom_id.startswith("beta_continue_"):
            await self.handle_continue(interaction, custom_id)
        elif custom_id.startswith("beta_accept_"):
            await self.handle_decision(interaction, custom_id, accept=True)
        elif custom_id.startswith("beta_reject_"):
            await self.handle_decision(interaction, custom_id, accept=False)

    async def handle_continue(self, interaction: discord.Interaction, custom_id: str):
        try:
            user_id = int(custom_id.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            return

        if interaction.user.id != user_id:
            return await interaction.response.send_message("❌ Das ist nicht deine Bewerbung.", ephemeral=True)

        apps = await self.config.guild(interaction.guild).applications()
        app_data = apps.get(str(user_id))
        if not app_data or app_data.get("status") != "pending_modal2":
            return await interaction.response.send_message(
                "❌ Deine vorherigen Antworten sind verloren gegangen. Bitte starte die Bewerbung neu.",
                ephemeral=True
            )

        await interaction.response.send_modal(BetaModal2(self, app_data["data"]))

    async def handle_decision(self, interaction: discord.Interaction, custom_id: str, accept: bool):
        try:
            user_id = int(custom_id.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            return

        guild = interaction.guild

        # Permission-Check: Nur Admins oder Team-Rolle
        if not (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild):
            return await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)

        apps = await self.config.guild(guild).applications()
        app_data = apps.get(str(user_id))
        if not app_data:
            return await interaction.response.send_message("❌ Keine Bewerbung gefunden.", ephemeral=True)

        if app_data.get("status") in ("accepted", "rejected"):
            return await interaction.response.send_message("❌ Diese Bewerbung wurde bereits bearbeitet.", ephemeral=True)

        member = guild.get_member(user_id)
        data = app_data["data"]

        if accept:
            # Rolle prüfen
            role_id = await self.config.guild(guild).accepted_role()
            if not role_id:
                return await interaction.response.send_message(
                    "❌ Es wurde noch keine Annahme-Rolle konfiguriert. Nutze `betarole @Rolle`.",
                    ephemeral=True
                )
            role = guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message("❌ Die konfigurierte Rolle existiert nicht mehr.", ephemeral=True)

            # Rolle vergeben
            if member:
                try:
                    await member.add_roles(role, reason=f"Beta-Bewerbung angenommen von {interaction.user}")
                except discord.Forbidden:
                    return await interaction.response.send_message(
                        "❌ Ich kann die Rolle nicht vergeben. Prüfe die Rollen-Hierarchie.",
                        ephemeral=True
                    )

            # DM senden
            if member:
                try:
                    dm_embed = discord.Embed(
                        title="🎉 Beta-Bewerbung angenommen!",
                        description=(
                            f"Herzlichen Glückwunsch, **{data['ic_name']}**!\n\n"
                            f"Deine Bewerbung für die Beta auf **Kreis Lindenberg** wurde **angenommen**.\n"
                            f"Du hast soeben die Rolle **{role.name}** erhalten.\n\n"
                            "Wir freuen uns auf dich!"
                        ),
                        color=discord.Color.green()
                    )
                    dm_embed.set_footer(text="Kreis Lindenberg Team")
                    await member.send(embed=dm_embed)
                except discord.Forbidden:
                    log.warning(f"[Beta] Konnte {member} keine DM senden.")

            # Status speichern
            async with self.config.guild(guild).applications() as apps2:
                if str(user_id) in apps2:
                    apps2[str(user_id)]["status"] = "accepted"
                    apps2[str(user_id)]["decided_by"] = interaction.user.id

            await interaction.response.send_message(f"✅ {member.mention if member else user_id} wurde **angenommen**.", ephemeral=True)
            await self.update_log_message(interaction, app_data, "accepted", interaction.user)

        else:
            # Reject
            async with self.config.guild(guild).rejected_users() as rejected:
                if user_id not in rejected:
                    rejected.append(user_id)

            async with self.config.guild(guild).applications() as apps2:
                if str(user_id) in apps2:
                    apps2[str(user_id)]["status"] = "rejected"
                    apps2[str(user_id)]["decided_by"] = interaction.user.id

            if member:
                try:
                    dm_embed = discord.Embed(
                        title="❌ Beta-Bewerbung abgelehnt",
                        description=(
                            f"Hallo **{data['ic_name']}**,\n\n"
                            "leider müssen wir dir mitteilen, dass deine Bewerbung für die Beta **abgelehnt** wurde.\n"
                            "Du kannst dich nicht erneut bewerben."
                        ),
                        color=discord.Color.red()
                    )
                    dm_embed.set_footer(text="Kreis Lindenberg Team")
                    await member.send(embed=dm_embed)
                except discord.Forbidden:
                    log.warning(f"[Beta] Konnte {member} keine DM senden.")

            await interaction.response.send_message(f"❌ {member.mention if member else user_id} wurde **abgelehnt**.", ephemeral=True)
            await self.update_log_message(interaction, app_data, "rejected", interaction.user)

    async def update_log_message(self, interaction: discord.Interaction, app_data: dict, decision: str, admin):
        """Aktualisiert die Log-Nachricht, um Buttons zu deaktivieren und Entscheidung anzuzeigen."""
        try:
            message = interaction.message
            if not message:
                return
            view = self.build_log_view(app_data["data"], decided=decision, admin=admin)
            await message.edit(view=view)
        except Exception as e:
            log.error(f"[Beta] Konnte Log-Nachricht nicht aktualisieren: {e}")

    # ---------------- LOG ----------------
    def build_log_view(self, data: dict, decided: str = None, admin=None):
        """Erstellt die Log-Nachricht (Buttons oder Entscheidung)."""
        view = LayoutView()

        if decided == "accepted":
            color = discord.Color.green()
        elif decided == "rejected":
            color = discord.Color.red()
        else:
            color = discord.Color.dark_blue()

        container = Container(accent_color=color)
        container.add_item(TextDisplay("## 📝 Neue Betabewerbung"))
        container.add_item(TextDisplay(f"**Bewerber:** <@{data['discord_user_id']}> (`{data['discord_user_id']}`)"))
        container.add_item(Separator())

        container.add_item(TextDisplay(f"**FiveM + Discord Name:**\n{data['fivem_discord']}"))
        container.add_item(TextDisplay(f"**IC Name:**\n{data['ic_name']}"))
        container.add_item(TextDisplay(f"**Fraktion:**\n{data['fraktion']}"))
        container.add_item(TextDisplay(f"**Plan IC:**\n{data['plan_ic']}"))
        container.add_item(TextDisplay(f"**Warum Beta:**\n{data['warum_beta']}"))
        container.add_item(TextDisplay(f"**Warum dich:**\n{data['warum_dich']}"))
        container.add_item(TextDisplay(f"**Bugs melden:**\n{data['bugs']}"))

        container.add_item(Separator())

        if decided is None:
            row = ActionRow()
            row.add_item(Button(
                label="Annehmen",
                style=discord.ButtonStyle.success,
                custom_id=f"beta_accept_{data['discord_user_id']}",
                emoji="✅"
            ))
            row.add_item(Button(
                label="Ablehnen",
                style=discord.ButtonStyle.danger,
                custom_id=f"beta_reject_{data['discord_user_id']}",
                emoji="❌"
            ))
            container.add_item(row)
        else:
            if decided == "accepted":
                container.add_item(TextDisplay(f"### ✅ Angenommen von {admin.mention}"))
            else:
                container.add_item(TextDisplay(f"### ❌ Abgelehnt von {admin.mention}"))

        view.add_item(container)
        return view

    async def send_application_log(self, guild, user, data):
        """Sendet die Bewerbung in den Log-Kanal und gibt die Message-ID zurück."""
        log_channel_id = await self.config.guild(guild).log_channel()
        if not log_channel_id:
            log.warning("[Beta] Kein Log-Kanal konfiguriert.")
            return None

        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return None

        view = self.build_log_view(data)

        try:
            msg = await log_channel.send(view=view)
            return msg.id
        except discord.Forbidden:
            log.error(f"[Beta] Keine Berechtigung für Log-Kanal {log_channel_id}.")
        except Exception as e:
            log.error(f"[Beta] Fehler beim Senden der Bewerbung: {e}")
        return None

    # ---------------- COMMANDS ----------------

    @commands.command(name="betasetup")
    @commands.admin_or_permissions(manage_guild=True)
    async def betasetup(self, ctx, channel: discord.TextChannel):
        """Richtet die Beta-Bewerbung in einem Kanal ein."""
        if not V2_AVAILABLE:
            return await ctx.send("❌ Deine discord.py Version unterstützt keine Components V2.")

        embed = discord.Embed(
            title="Betabewerbung - Kreis Lindenberg",
            description="Die Bewerbung wird initialisiert...",
            color=discord.Color.dark_blue()
        )
        msg = await channel.send(embed=embed)

        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).message_id.set(msg.id)

        view = BetaApplicationView()
        try:
            await msg.edit(content=None, embed=None, view=view)
        except Exception as e:
            log.error(f"[Beta] Fehler beim Editieren: {e}")
            return await ctx.send(f"❌ Fehler beim Erstellen der Nachricht: {e}")

        await ctx.send(f"✅ Beta-Bewerbung wurde in {channel.mention} eingerichtet.")

    @commands.command(name="betalog")
    @commands.admin_or_permissions(manage_guild=True)
    async def betalog(self, ctx, channel: discord.TextChannel = None):
        """Setzt den Log-Kanal für eingegangene Bewerbungen."""
        if channel is None:
            await self.config.guild(ctx.guild).log_channel.set(None)
            return await ctx.send("✅ Bewerbungs-Log-Kanal entfernt.")
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Bewerbungen werden jetzt in {channel.mention} gesendet.")

    @commands.command(name="betarole")
    @commands.admin_or_permissions(manage_guild=True)
    async def betarole(self, ctx, role: discord.Role = None):
        """Setzt die Rolle, die bei Annahme der Bewerbung vergeben wird."""
        if role is None:
            await self.config.guild(ctx.guild).accepted_role.set(None)
            return await ctx.send("✅ Annahme-Rolle entfernt.")
        if role.is_default():
            return await ctx.send("❌ `@everyone` kann nicht verwendet werden.")
        await self.config.guild(ctx.guild).accepted_role.set(role.id)
        await ctx.send(f"✅ Bei Annahme wird jetzt die Rolle {role.mention} vergeben.")

    @commands.command(name="betaupdate")
    @commands.admin_or_permissions(manage_guild=True)
    async def betaupdate(self, ctx):
        """Erzwingt ein manuelles Update der Bewerbungsnachricht."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        message_id = await self.config.guild(ctx.guild).message_id()

        if not channel_id or not message_id:
            return await ctx.send("❌ Die Bewerbung wurde noch nicht eingerichtet.")

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("❌ Der Kanal existiert nicht mehr.")

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return await ctx.send("❌ Die Nachricht existiert nicht mehr. Bitte `betasetup` erneut ausführen.")

        view = BetaApplicationView()
        try:
            await message.edit(content=None, embed=None, view=view)
            await ctx.send("✅ Bewerbungs-Nachricht wurde aktualisiert.")
        except Exception as e:
            await ctx.send(f"❌ Fehler beim Aktualisieren: {e}")

    @commands.command(name="betaunreject")
    @commands.admin_or_permissions(manage_guild=True)
    async def betaunreject(self, ctx, member: discord.Member):
        """Hebt die Sperre eines abgelehnten Users auf."""
        async with self.config.guild(ctx.guild).rejected_users() as rejected:
            if member.id in rejected:
                rejected.remove(member.id)
                await ctx.send(f"✅ {member.mention} kann sich wieder bewerben.")
            else:
                await ctx.send(f"❌ {member.mention} ist nicht gesperrt.")

    @commands.command(name="betaunapply")
    @commands.admin_or_permissions(manage_guild=True)
    async def betaunapply(self, ctx, member: discord.Member):
        """Löscht eine laufende/entschiedene Bewerbung eines Users."""
        async with self.config.guild(ctx.guild).applications() as apps:
            if str(member.id) in apps:
                del apps[str(member.id)]
                await ctx.send(f"✅ Bewerbung von {member.mention} wurde gelöscht.")
            else:
                await ctx.send(f"❌ Keine Bewerbung von {member.mention} gefunden.")

    @commands.command(name="betastats")
    @commands.admin_or_permissions(manage_guild=True)
    async def betastats(self, ctx):
        """Zeigt eine Übersicht aller Bewerbungen."""
        apps = await self.config.guild(ctx.guild).applications()
        pending_review = [uid for uid, a in apps.items() if a.get("status") == "pending_review"]
        pending_modal2 = [uid for uid, a in apps.items() if a.get("status") == "pending_modal2"]
        accepted = [uid for uid, a in apps.items() if a.get("status") == "accepted"]
        rejected_apps = [uid for uid, a in apps.items() if a.get("status") == "rejected"]
        rejected_list = await self.config.guild(ctx.guild).rejected_users()

        embed = discord.Embed(title="📊 Beta-Bewerbungen — Statistik", color=discord.Color.dark_blue())
        embed.add_field(name="⏳ In Prüfung", value=f"**{len(pending_review)}**", inline=True)
        embed.add_field(name="✍️ Teil 2 offen", value=f"**{len(pending_modal2)}**", inline=True)
        embed.add_field(name="✅ Angenommen", value=f"**{len(accepted)}**", inline=True)
        embed.add_field(name="❌ Abgelehnt (Bewerbung)", value=f"**{len(rejected_apps)}**", inline=True)
        embed.add_field(name="🚫 Gesperrte User", value=f"**{len(rejected_list)}**", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="betahelp")
    async def betahelp(self, ctx):
        """Zeigt die Befehle für das Beta-Bewerbungssystem an."""
        embed = discord.Embed(title="📝 Beta-Bewerbung — Befehlsübersicht", color=discord.Color.dark_blue())
        embed.add_field(
            name="⚙️ Setup",
            value=(
                "`betasetup #kanal` — Bewerbung einrichten\n"
                "`betalog #kanal` — Log-Kanal für Bewerbungen setzen\n"
                "`betarole @rolle` — Rolle bei Annahme setzen\n"
                "`betaupdate` — Nachricht manuell aktualisieren"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ Verwaltung",
            value=(
                "`betaunreject @user` — Sperre eines Users aufheben\n"
                "`betaunapply @user` — Bewerbung eines Users löschen\n"
                "`betastats` — Übersicht aller Bewerbungen"
            ),
            inline=False,
        )
        embed.set_footer(text="User können sich über den Button in der Nachricht bewerben.")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Beta(bot))
