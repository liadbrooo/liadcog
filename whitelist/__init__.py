import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import re
import asyncio

class FiveMWhitelist(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "log_channel": None,
            "wl_role": None,
            "ping_role": None,
            "extra_wl_roles": []
        }
        self.config.register_guild(**default_guild)
        
        # Registriere persistente Views
        bot.add_view(WhitelistButtonView(self.config))
        bot.add_view(ApplicationActionsView(self.config))

    # Hilfsfunktion für Berechtigungsprüfung
    async def check_perms(self, ctx_or_interaction) -> bool:
        if hasattr(ctx_or_interaction, 'guild_permissions'): # Context
            user = ctx_or_interaction.author
            guild = ctx_or_interaction.guild
            if user.guild_permissions.manage_guild:
                return True
        else: # Interaction
            user = ctx_or_interaction.user
            guild = ctx_or_interaction.guild
            if user.guild_permissions.manage_roles:
                return True

        ping_role_id = await self.config.guild(guild).ping_role()
        if ping_role_id and guild.get_role(ping_role_id) in user.roles:
            return True
            
        extra_roles = await self.config.guild(guild).extra_wl_roles()
        for role_id in extra_roles:
            if guild.get_role(role_id) in user.roles:
                return True
                
        return False

    @commands.group(name="lwhitelist", invoke_without_command=False)
    @commands.admin_or_permissions(manage_guild=True)
    async def lwhitelist_group(self, ctx: commands.Context):
        """Einstellungen für das FiveM Whitelist System."""
        pass

    # --- NEUE MANUELLE BEFEHLE ---

    @commands.command(name="lw")
    async def manual_add_wl(self, ctx: commands.Context, user_id: int):
        """Fügt einem User manuell die Whitelist-Rolle hinzu (z.B. !lw 123456789)."""
        if not await self.check_perms(ctx):
            return await ctx.send("❌ Du hast keine Berechtigung, dies zu tun.", delete_after=10)
            
        wl_role_id = await self.config.guild(ctx.guild).wl_role()
        if not wl_role_id:
            return await ctx.send("❌ Es ist keine Whitelist-Rolle in den Einstellungen hinterlegt.")
        
        wl_role = ctx.guild.get_role(wl_role_id)
        if not wl_role:
            return await ctx.send("❌ Die hinterlegte Whitelist-Rolle existiert nicht mehr.")
            
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
            await ctx.send(f"✅ {member.mention} wurde erfolgreich die Whitelist-Rolle {wl_role.mention} gegeben.")
        except discord.Forbidden:
            await ctx.send("❌ Ich habe keine Berechtigung, diese Rolle zu vergeben. Bitte prüfe meine Rollen/Rechte.")

    @commands.command(name="luw")
    async def manual_remove_wl(self, ctx: commands.Context, user_id: int):
        """Entfernt einem User manuell die Whitelist-Rolle (z.B. !luw 123456789)."""
        if not await self.check_perms(ctx):
            return await ctx.send("❌ Du hast keine Berechtigung, dies zu tun.", delete_after=10)
            
        wl_role_id = await self.config.guild(ctx.guild).wl_role()
        if not wl_role_id:
            return await ctx.send("❌ Es ist keine Whitelist-Rolle in den Einstellungen hinterlegt.")
            
        wl_role = ctx.guild.get_role(wl_role_id)
        if not wl_role:
            return await ctx.send("❌ Die hinterlegte Whitelist-Rolle existiert nicht mehr.")
            
        try:
            member = await ctx.guild.fetch_member(user_id)
        except discord.NotFound:
            return await ctx.send("❌ User nicht auf diesem Server gefunden.")
            
        if wl_role not in member.roles:
            return await ctx.send("ℹ️ Dieser User hat die Whitelist gar nicht.")
            
        try:
            await member.remove_roles(wl_role)
            await ctx.send(f"✅ {member.mention} wurde die Whitelist-Rolle {wl_role.mention} entfernt.")
        except discord.Forbidden:
            await ctx.send("❌ Ich habe keine Berechtigung, diese Rolle zu entfernen. Bitte prüfe meine Rollen/Rechte.")

    # --- SETUP WIZARD & SETTINGS ---

    @lwhitelist_group.command(name="wizard")
    async def setup_wizard(self, ctx: commands.Context):
        """Startet den interaktiven Setup-Assistenten."""
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("**[1/4] Setup-Assistent:**\nBitte mentione den Channel, in dem die Bewerbungen landen sollen (z.B. `#team-bewerbungen`).")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Zeit abgelaufen. Setup abgebrochen.")
        if not msg.channel_mentions:
            return await ctx.send("❌ Kein Channel erwähnt. Setup abgebrochen.")
        log_channel = msg.channel_mentions[0]
        await self.config.guild(ctx.guild).log_channel.set(log_channel.id)

        await ctx.send(f"✅ Log-Channel gesetzt auf {log_channel.mention}.\n\n**[2/4]** Bitte mentione jetzt die Rolle, die User erhalten sollen, wenn sie angenommen werden (z.B. `@Whitelist`).")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Zeit abgelaufen. Setup abgebrochen.")
        if not msg.role_mentions:
            return await ctx.send("❌ Keine Rolle erwähnt. Setup abgebrochen.")
        wl_role = msg.role_mentions[0]
        await self.config.guild(ctx.guild).wl_role.set(wl_role.id)

        await ctx.send(f"✅ Whitelist-Rolle gesetzt auf {wl_role.mention}.\n\n**[3/4]** Welche Rolle soll bei neuen Bewerbungen gepingt werden? (z.B. `@Support`). Schreibe `skip`, falls keine gepingt werden soll.")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Zeit abgelaufen. Setup abgebrochen.")
        if msg.content.lower() == "skip":
            await self.config.guild(ctx.guild).ping_role.set(None)
            await ctx.send("✅ Keine Ping-Rolle festgelegt.")
        elif msg.role_mentions:
            ping_role = msg.role_mentions[0]
            await self.config.guild(ctx.guild).ping_role.set(ping_role.id)
            await ctx.send(f"✅ Ping-Rolle gesetzt auf {ping_role.mention}.")
        else:
            await ctx.send("❌ Ungültige Eingabe. Überspringe Ping-Rolle.")
            await self.config.guild(ctx.guild).ping_role.set(None)

        await ctx.send("**[4/4]** Gib nun alle **weiteren** Rollen an, die Bewerbungen annehmen/ablehnen dürfen, aber NICHT gepingt werden sollen. Mentione sie einfach alle in einer Nachricht (z.B. `@Admin @Leitung`). Schreibe `skip`, falls es keine gibt.")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            return await ctx.send("⏱️ Zeit abgelaufen. Setup abgebrochen.")
        if msg.content.lower() == "skip" or not msg.role_mentions:
            await self.config.guild(ctx.guild).extra_wl_roles.set([])
            await ctx.send("✅ Keine extra Whitelister-Rollen hinzugefügt.")
        else:
            extra_roles = [r.id for r in msg.role_mentions]
            await self.config.guild(ctx.guild).extra_wl_roles.set(extra_roles)
            roles_str = ", ".join([r.mention for r in msg.role_mentions])
            await ctx.send(f"✅ Extra Whitelister-Rollen gesetzt: {roles_str}")

        await ctx.send("🎉 **Setup erfolgreich abgeschlossen!** Du kannst nun das Panel mit `!lwhitelist setup` posten.")

    @lwhitelist_group.command(name="setchannel")
    async def set_log_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Setzt den Channel, in dem die Bewerbungen an das Team gesendet werden."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Bewerbungs-Channel wurde auf {channel.mention} gesetzt.")

    @lwhitelist_group.command(name="setrole")
    async def set_wl_role(self, ctx: commands.Context, role: discord.Role):
        """Setzt die Rolle, die User bei Annahme erhalten."""
        await self.config.guild(ctx.guild).wl_role.set(role.id)
        await ctx.send(f"✅ Whitelist-Rolle wurde auf {role.mention} gesetzt.")

    @lwhitelist_group.command(name="setpingrole")
    async def set_ping_role(self, ctx: commands.Context, role: discord.Role = None):
        """Setzt die Rolle, die bei neuen Bewerbungen gepingt wird."""
        await self.config.guild(ctx.guild).ping_role.set(role.id if role else None)
        if role:
            await ctx.send(f"✅ Ping-Rolle wurde auf {role.mention} gesetzt.")
        else:
            await ctx.send("✅ Ping-Rolle wurde entfernt.")

    @lwhitelist_group.command(name="addwlrole")
    async def add_wl_role(self, ctx: commands.Context, role: discord.Role):
        """Fügt eine Rolle hinzu, die Bewerbungen bearbeiten darf (ohne Ping)."""
        async with self.config.guild(ctx.guild).extra_wl_roles() as extra_roles:
            if role.id not in extra_roles:
                extra_roles.append(role.id)
        await ctx.send(f"✅ Die Rolle {role.mention} kann nun auch Bewerbungen annehmen/ablehnen.")

    @lwhitelist_group.command(name="removewlrole")
    async def remove_wl_role(self, ctx: commands.Context, role: discord.Role):
        """Entfernt eine Extra-Whitelister-Rolle."""
        async with self.config.guild(ctx.guild).extra_wl_roles() as extra_roles:
            if role.id in extra_roles:
                extra_roles.remove(role.id)
        await ctx.send(f"✅ Die Rolle {role.mention} wurde entfernt.")

    @lwhitelist_group.command(name="settings")
    async def show_settings(self, ctx: commands.Context):
        """Zeigt die aktuellen Einstellungen an."""
        settings = await self.config.guild(ctx.guild).all()
        
        log_ch = ctx.guild.get_channel(settings["log_channel"]) if settings["log_channel"] else "Nicht gesetzt"
        wl_r = ctx.guild.get_role(settings["wl_role"]) if settings["wl_role"] else "Nicht gesetzt"
        ping_r = ctx.guild.get_role(settings["ping_role"]) if settings["ping_role"] else "Nicht gesetzt"
        extra_rs = [ctx.guild.get_role(r).mention for r in settings["extra_wl_roles"] if ctx.guild.get_role(r)]
        extra_str = ", ".join(extra_rs) if extra_rs else "Keine gesetzt"

        embed = discord.Embed(title="⚙️ Whitelist System Einstellungen", color=discord.Color.blue())
        embed.add_field(name="Log Channel", value=log_ch.mention if isinstance(log_ch, discord.TextChannel) else log_ch, inline=False)
        embed.add_field(name="Whitelist Rolle", value=wl_r.mention if isinstance(wl_r, discord.Role) else wl_r, inline=False)
        embed.add_field(name="Ping Rolle", value=ping_r.mention if isinstance(ping_r, discord.Role) else ping_r, inline=False)
        embed.add_field(name="Extra Whitelister Rollen", value=extra_str, inline=False)
        
        await ctx.send(embed=embed)

    @lwhitelist_group.command(name="setup")
    async def setup_panel(self, ctx: commands.Context):
        """Sendet das Panel, auf das User klicken können, um das Formular zu öffnen."""
        embed = discord.Embed(
            title="🚨 FiveM Whitelist Bewerbung",
            description=(
                "Willkommen auf unserem Server!\n\n"
                "Um auf unseren Server zu kommen und die Whitelist zu erhalten, "
                "musst du ein kurzes Formular ausfüllen.\n\n"
                "Klicke unten auf den Button, um deine Bewerbung zu starten."
            ),
            color=discord.Color.blue()
        )
        view = WhitelistButtonView(self.config)
        await ctx.send(embed=embed, view=view)


# --- UI Komponenten ---

class WhitelistButtonView(discord.ui.View):
    def __init__(self, config: Config):
        super().__init__(timeout=None)
        self.config = config

    @discord.ui.button(label="Bewerbung starten", style=discord.ButtonStyle.primary, custom_id="fivem_wl_start_v7", emoji="📝")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        wl_role_id = await self.config.guild(interaction.guild).wl_role()
        if wl_role_id:
            wl_role = interaction.guild.get_role(wl_role_id)
            if wl_role and wl_role in interaction.user.roles:
                return await interaction.response.send_message("Du bist bereits gewhitelisted! 🎉", ephemeral=True)

        modal = WhitelistModal(self.config)
        await interaction.response.send_modal(modal)


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
        log_channel_id = await self.config.guild(interaction.guild).log_channel()
        if not log_channel_id:
            return await interaction.response.send_message("Fehler: Admin hat keinen Log-Channel festgelegt.", ephemeral=True)

        log_channel = interaction.guild.get_channel(log_channel_id)
        if not log_channel:
            return await interaction.response.send_message("Fehler: Log-Channel nicht gefunden.", ephemeral=True)

        embed = discord.Embed(
            title="📋 Neue Whitelist Bewerbung",
            color=discord.Color.orange(),
            description=f"**Bewerber:** {interaction.user.mention} (`{interaction.user.id}`)"
        )
        embed.add_field(name="Dein Name (OOC)", value=self.ooc_name.value, inline=True)
        embed.add_field(name="Alter (OOC)", value=self.alter.value, inline=True)
        embed.add_field(name="\u200B", value="\u200B", inline=True)
        
        embed.add_field(name="Roleplay-Erfahrung", value=self.rp_erfahrung.value, inline=False)
        embed.add_field(name="IC Pläne", value=self.ic_plans.value, inline=False)
        embed.add_field(name="Charakter-Geschichte", value=self.charakter_geschichte.value, inline=False)
        
        embed.set_footer(text=f"Bewerbung von {interaction.user}")

        ping_role_id = await self.config.guild(interaction.guild).ping_role()
        content = f"🔔 Neue Bewerbung von {interaction.user.mention}"
        if ping_role_id:
            role = interaction.guild.get_role(ping_role_id)
            if role:
                content += f"\n{role.mention}"

        view = ApplicationActionsView(self.config)
        await log_channel.send(content=content, embed=embed, view=view)
        
        await interaction.response.send_message("✅ Deine Bewerbung wurde erfolgreich an das Team gesendet! Bitte habe etwas Geduld.", ephemeral=True)


class ApplicationActionsView(discord.ui.View):
    def __init__(self, config: Config):
        super().__init__(timeout=None)
        self.config = config
        self.cog_ref = None # Placeholder, we use static method via cog instance check

    async def get_applicant(self, interaction: discord.Interaction):
        match = re.search(r"`(\d+)`", interaction.message.embeds[0].description)
        if match:
            user_id = int(match.group(1))
            return interaction.guild.get_member(user_id)
        return None

    async def check_perms(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_roles:
            return True
            
        ping_role_id = await self.config.guild(interaction.guild).ping_role()
        if ping_role_id and interaction.guild.get_role(ping_role_id) in interaction.user.roles:
            return True
            
        extra_roles = await self.config.guild(interaction.guild).extra_wl_roles()
        for role_id in extra_roles:
            if interaction.guild.get_role(role_id) in interaction.user.roles:
                return True
                
        return False

    @discord.ui.button(label="Annehmen", style=discord.ButtonStyle.success, custom_id="fivem_wl_accept_v7", emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_perms(interaction):
            return await interaction.response.send_message("❌ Du hast keine Berechtigung, Bewerbungen zu bearbeiten.", ephemeral=True)

        applicant = await self.get_applicant(interaction)
        wl_role_id = await self.config.guild(interaction.guild).wl_role()
        wl_role = interaction.guild.get_role(wl_role_id)

        if not applicant or not wl_role:
            return await interaction.response.send_message("Fehler: User oder Rolle konnte nicht gefunden werden.", ephemeral=True)

        try:
            await applicant.add_roles(wl_role)
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Ich habe keine Berechtigung, dem User die Rolle zu geben.", ephemeral=True)

        dm_failed = False
        try:
            await applicant.send(f"🎉 **Herzlichen Glückwunsch!**\nDeine Whitelist-Bewerbung auf **{interaction.guild.name}** wurde angenommen! Du hast nun Zugriff auf den Server.")
        except discord.Forbidden:
            dm_failed = True

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = "✅ Angenommen"
        embed.add_field(name="⚙️ Admin-Aktion", value=f"Angenommen von: {interaction.user.mention}", inline=False)
        
        await interaction.response.edit_message(content=f"🔔 Angenommen von {interaction.user.mention}", embed=embed, view=None)
        
        if dm_failed:
            await interaction.followup.send("⚠️ Der User wurde angenommen, hat aber seine **DMs gesperrt**! Bitte informiere ihn manuell.", ephemeral=True)

    @discord.ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="fivem_wl_reject_v7", emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_perms(interaction):
            return await interaction.response.send_message("❌ Du hast keine Berechtigung, Bewerbungen zu bearbeiten.", ephemeral=True)

        applicant = await self.get_applicant(interaction)
        if not applicant:
            return await interaction.response.send_message("Fehler: User konnte nicht gefunden werden.", ephemeral=True)

        modal = RejectReasonModal(applicant, interaction.message, interaction.guild.name, interaction.user)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Rückfragen", style=discord.ButtonStyle.secondary, custom_id="fivem_wl_questions_v7", emoji="❓")
    async def questions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_perms(interaction):
            return await interaction.response.send_message("❌ Du hast keine Berechtigung, Bewerbungen zu bearbeiten.", ephemeral=True)

        applicant = await self.get_applicant(interaction)
        if not applicant:
            return await interaction.response.send_message("Fehler: User konnte nicht gefunden werden.", ephemeral=True)

        try:
            await applicant.send(
                f"❓ **Rückfragen zu deiner Bewerbung**\n\n"
                f"Hallo {applicant.mention}, wir haben noch ein paar Fragen zu deiner Whitelist-Anfrage. "
                f"Bitte komm in den Support-Warteraum oder eröffne ein Allgemeines-Ticket."
            )
        except discord.Forbidden:
            # Wenn DMs gesperrt sind, Buttons NICHT entfernen, damit es das Team nochmal versuchen kann oder manuell anschreibt
            return await interaction.response.send_message("⚠️ Dieser User hat seine **DMs gesperrt**! Ich konnte ihn nicht anschreiben. Bitte kontaktiere ihn anderweitig (z.B. über einen öffentlichen Channel).", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.gold()
        embed.title = "❓ Rückfragen gestellt"
        embed.add_field(name="⚙️ Admin-Aktion", value=f"Rückfragen gestellt von: {interaction.user.mention}", inline=False)
        
        await interaction.response.edit_message(content=f"🔔 Rückfragen von {interaction.user.mention}", embed=embed, view=None)


class RejectReasonModal(discord.ui.Modal, title="Grund für Ablehnung"):
    def __init__(self, applicant: discord.Member, original_message: discord.Message, guild_name: str, admin: discord.User):
        super().__init__()
        self.applicant = applicant
        self.original_message = original_message
        self.guild_name = guild_name
        self.admin = admin

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
                f"Du kannst es in 14 Tagen erneut versuchen."
            )
        except discord.Forbidden:
            dm_failed = True

        embed = self.original_message.embeds[0]
        embed.color = discord.Color.red()
        embed.title = "❌ Abgelehnt"
        embed.add_field(name="⚙️ Admin-Aktion", value=f"Abgelehnt von: {self.admin.mention}\n**Grund:** {self.reason.value}", inline=False)
        
        await self.original_message.edit(content=f"🔔 Abgelehnt von {self.admin.mention}", embed=embed, view=None)
        
        if dm_failed:
            await interaction.response.send_message("⚠️ Der Bewerber wurde abgelehnt, hat aber seine **DMs gesperrt** und konnte nicht informiert werden.", ephemeral=True)
        else:
            await interaction.response.send_message("Der Bewerber wurde abgelehnt und informiert.", ephemeral=True)


async def setup(bot: Red):
    await bot.add_cog(FiveMWhitelist(bot))
