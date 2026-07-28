import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import re

class FiveMWhitelist(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "log_channel": None,
            "wl_role": None
        }
        self.config.register_guild(**default_guild)
        
        # Registriere persistente Views
        bot.add_view(WhitelistButtonView(self.config))
        bot.add_view(ApplicationActionsView(self.config))

    @commands.group(name="lwhitelist")
    @commands.admin_or_permissions(manage_guild=True)
    async def lwhitelist_group(self, ctx: commands.Context):
        """Einstellungen für das FiveM Whitelist System."""
        pass

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

    @discord.ui.button(label="Bewerbung starten", style=discord.ButtonStyle.primary, custom_id="fivem_wl_start_v4", emoji="📝")
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

    # Feld 1: Name
    ooc_name = discord.ui.TextInput(
        label="Dein Name (OOC)",
        placeholder="Dein echter Vorname (z.B. Max)",
        required=True,
        max_length=30
    )
    # Feld 2: Alter
    alter = discord.ui.TextInput(
        label="Dein Alter (OOC)",
        placeholder="z.B. 22",
        required=True,
        min_length=2,
        max_length=3
    )
    # Feld 3: RP Erfahrung
    rp_erfahrung = discord.ui.TextInput(
        label="Deine Roleplay-Erfahrung",
        placeholder="Seit wann spielst du RP? Welche Server?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )
    # Feld 4: IC Pläne
    ic_plans = discord.ui.TextInput(
        label="Was planst du auf dem Server? (IC)",
        placeholder="z.B. Polizei, Arzt, Gangmitglied, Mechaniker...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
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
        # Felder sauber anordnen
        embed.add_field(name="Dein Name (OOC)", value=self.ooc_name.value, inline=True)
        embed.add_field(name="Alter (OOC)", value=self.alter.value, inline=True)
        embed.add_field(name="\u200B", value="\u200B", inline=True) # Leerzeile für sauberes Layout
        
        embed.add_field(name="Roleplay-Erfahrung", value=self.rp_erfahrung.value, inline=False)
        embed.add_field(name="IC Pläne", value=self.ic_plans.value, inline=False)
        
        embed.set_footer(text=f"Bewerbung von {interaction.user}")

        view = ApplicationActionsView(self.config)
        await log_channel.send(content=f"🔔 Neue Bewerbung von {interaction.user.mention}", embed=embed, view=view)
        
        await interaction.response.send_message("✅ Deine Bewerbung wurde erfolgreich an das Team gesendet! Bitte habe etwas Geduld.", ephemeral=True)


class ApplicationActionsView(discord.ui.View):
    def __init__(self, config: Config):
        super().__init__(timeout=None)
        self.config = config

    async def get_applicant(self, interaction: discord.Interaction):
        match = re.search(r"`(\d+)`", interaction.message.embeds[0].description)
        if match:
            user_id = int(match.group(1))
            return interaction.guild.get_member(user_id)
        return None

    @discord.ui.button(label="Annehmen", style=discord.ButtonStyle.success, custom_id="fivem_wl_accept_v4", emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            return await interaction.response.send_message("Du hast keine Rechte dafür.", ephemeral=True)

        applicant = await self.get_applicant(interaction)
        wl_role_id = await self.config.guild(interaction.guild).wl_role()
        wl_role = interaction.guild.get_role(wl_role_id)

        if not applicant or not wl_role:
            return await interaction.response.send_message("Fehler: User oder Rolle konnte nicht gefunden werden.", ephemeral=True)

        try:
            await applicant.add_roles(wl_role)
            await applicant.send(f"🎉 **Herzlichen Glückwunsch!**\nDeine Whitelist-Bewerbung auf **{interaction.guild.name}** wurde angenommen! Du hast nun Zugriff auf den Server.")
        except discord.Forbidden:
            pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = "✅ Angenommen"
        embed.add_field(name="⚙️ Admin-Aktion", value=f"Angenommen von: {interaction.user.mention}", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Ablehnen", style=discord.ButtonStyle.danger, custom_id="fivem_wl_reject_v4", emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            return await interaction.response.send_message("Du hast keine Rechte dafür.", ephemeral=True)

        applicant = await self.get_applicant(interaction)
        if not applicant:
            return await interaction.response.send_message("Fehler: User konnte nicht gefunden werden.", ephemeral=True)

        # Öffnet ein neues Modal für den Ablehnungsgrund
        modal = RejectReasonModal(applicant, interaction.message, interaction.guild.name, interaction.user)
        await interaction.response.send_modal(modal)


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
        # DM an den User schicken
        try:
            await self.applicant.send(
                f"❌ **Bedauerlicherweise...**\n"
                f"Deine Whitelist-Bewerbung auf **{self.guild_name}** wurde leider abgelehnt.\n\n"
                f"**Grund:** {self.reason.value}\n\n"
                f"Du kannst es in 14 Tagen erneut versuchen."
            )
        except discord.Forbidden:
            pass # User hat DMs gesperrt

        # Embed im Team Channel updaten
        embed = self.original_message.embeds[0]
        embed.color = discord.Color.red()
        embed.title = "❌ Abgelehnt"
        embed.add_field(name="⚙️ Admin-Aktion", value=f"Abgelehnt von: {self.admin.mention}\n**Grund:** {self.reason.value}", inline=False)
        
        await self.original_message.edit(embed=embed, view=None)
        await interaction.response.send_message("Der Bewerber wurde abgelehnt und informiert.", ephemeral=True)


async def setup(bot: Red):
    await bot.add_cog(FiveMWhitelist(bot))
