import discord
from redbot.core import commands, Config
from datetime import datetime, timedelta

class AntiRaid(commands.Cog):
    """Schutz-System, das Teamler kickt, die zu viele Leute in kurzer Zeit kicken/bannen."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        default_guild = {
            "enabled": False,
            "threshold": 3,          # Wie viele Kicks/Bans erlaubt sind
            "timeframe": 60,         # Innerhalb wievieler Sekunden
            "excluded_roles": []     # Liste von Rollen-IDs, die ausgeschlossen sind
        }
        
        self.config.register_guild(**default_guild)
        
        # In-Memory Cache: {guild_id: {mod_id: [datetime, datetime, ...]}}
        self.action_cache = {}

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        # Wir reagieren nur auf Kicks und Bans
        if entry.action not in (discord.AuditLogAction.kick, discord.AuditLogAction.ban):
            return

        guild = entry.guild
        mod = entry.user # Der Teamler, der die Aktion ausgeführt hat
        
        # Sicherheit: Bot sich selbst kicken? Oder der Serverbesitzer? Nein.
        if mod == guild.me or mod == guild.owner:
            return

        # Prüfen ob das System auf dem Server aktiv ist
        if not await self.config.guild(guild).enabled():
            return

        # Prüfen ob der Teamler eine ausgeschlossene Rolle hat
        excluded_role_ids = await self.config.guild(guild).excluded_roles()
        # Vergleiche die Rollen-IDs des Moderators mit den ausgeschlossenen Rollen-IDs
        mod_role_ids = [role.id for role in mod.roles]
        if any(role_id in excluded_role_ids for role_id in mod_role_ids):
            return

        # Wenn der Bot keine Audit Logs lesen darf, abbrechen
        if not guild.me.guild_permissions.view_audit_log:
            return

        threshold = await self.config.guild(guild).threshold()
        timeframe = await self.config.guild(guild).timeframe()

        # Cache für den Server initialisieren, falls nicht vorhanden
        if guild.id not in self.action_cache:
            self.action_cache[guild.id] = {}
        
        # Cache für den Teamler initialisieren
        if mod.id not in self.action_cache[guild.id]:
            self.action_cache[guild.id][mod.id] = []

        now = datetime.utcnow()
        
        # Veraltete Einträge aus dem Cache entfernen (älter als das Zeitfenster)
        self.action_cache[guild.id][mod.id] = [
            t for t in self.action_cache[guild.id][mod.id] 
            if now - t < timedelta(seconds=timeframe)
        ]

        # Neue Aktion hinzufügen
        self.action_cache[guild.id][mod.id].append(now)

        # Prüfen, ob das Limit überschritten wurde
        if len(self.action_cache[guild.id][mod.id]) >= threshold:
            # Cache zurücksetzen
            self.action_cache[guild.id][mod.id] = []
            
            # Versuchen, den Teamler zu kicken
            try:
                await mod.kick(reason="Anti-Raid: Zu viele Kicks/Bans in kurzer Zeit!")
                channel = guild.system_channel
                if channel and channel.permissions_for(guild.me).send_messages:
                    embed = discord.Embed(
                        title="⚠️ Anti-Raid System",
                        description=f"{mod.mention} wurde gekickt, da er das Limit von **{threshold}** Aktionen innerhalb von **{timeframe}** Sekunden überschritten hat!",
                        color=discord.Color.red()
                    )
                    await channel.send(embed=embed)
            except discord.Forbidden:
                # Der Bot hat keine Rechte, den Teamler zu kicken (Rollenhierarchie)
                pass
            except discord.HTTPException:
                pass

    # --- Einstellungs-Befehle ---

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def antiraid(self, ctx):
        """Einstellungen für das Anti-Raid Schutzsystem."""
        pass

    @antiraid.command()
    async def toggle(self, ctx):
        """Aktiviert oder Deaktiviert das Anti-Raid System."""
        current = await self.config.guild(ctx.guild).enabled()
        new_state = not current
        await self.config.guild(ctx.guild).enabled.set(new_state)
        
        status = "aktiviert ✅" if new_state else "deaktiviert ❌"
        await ctx.send(f"Anti-Raid System wurde {status}.")

    @antiraid.command()
    async def threshold(self, ctx, amount: int):
        """Legt fest, wie viele Kicks/Bans erlaubt sind (Standard: 3)."""
        if amount < 1:
            return await ctx.send("Das Limit muss mindestens 1 sein.")
        
        await self.config.guild(ctx.guild).threshold.set(amount)
        await ctx.send(f"Limit wurde auf **{amount}** Kicks/Bans gesetzt.")

    @antiraid.command()
    async def timeframe(self, ctx, seconds: int):
        """Legt das Zeitfenster in Sekunden fest (Standard: 60)."""
        if seconds < 5:
            return await ctx.send("Das Zeitfenster muss mindestens 5 Sekunden betragen.")
        
        await self.config.guild(ctx.guild).timeframe.set(seconds)
        await ctx.send(f"Zeitfenster wurde auf **{seconds}** Sekunden gesetzt.")

    @antiraid.command()
    async def exclude(self, ctx, role: discord.Role):
        """Schließt eine Rolle vom Anti-Raid System aus."""
        # Verhindern, dass @everyone ausgeschlossen wird
        if role.is_default():
            return await ctx.send("Du kannst die `@everyone` Rolle nicht ausschließen, sonst greift das System nie!")

        async with self.config.guild(ctx.guild).excluded_roles() as excluded:
            if role.id not in excluded:
                excluded.append(role.id)
                await ctx.send(f"Die Rolle {role.mention} wurde vom Anti-Raid System ausgeschlossen. Jeder mit dieser Rolle wird nicht mehr gekickt.")
            else:
                await ctx.send(f"Die Rolle {role.mention} ist bereits ausgeschlossen.")

    @antiraid.command()
    async def unexclude(self, ctx, role: discord.Role):
        """Nimmt eine Rolle wieder in das Anti-Raid System auf."""
        async with self.config.guild(ctx.guild).excluded_roles() as excluded:
            if role.id in excluded:
                excluded.remove(role.id)
                await ctx.send(f"Die Rolle {role.mention} wird nun wieder vom Anti-Raid System überwacht.")
            else:
                await ctx.send(f"Die Rolle {role.mention} war nicht ausgeschlossen.")

    @antiraid.command()
    async def settings(self, ctx):
        """Zeigt die aktuellen Einstellungen an."""
        data = await self.config.guild(ctx.guild).all()
        
        # Rollen-Namen für die Anzeige aufbereiten
        excluded_roles = []
        for role_id in data['excluded_roles']:
            role = ctx.guild.get_role(role_id)
            if role:
                excluded_roles.append(role.mention)
            else:
                # Falls die Rolle gelöscht wurde, zeige die ID an
                excluded_roles.append(f"Gelöschte Rolle (ID: {role_id})")
        
        embed = discord.Embed(title="Anti-Raid Einstellungen", color=await ctx.embed_color())
        embed.add_field(name="Aktiviert", value="Ja ✅" if data['enabled'] else "Nein ❌", inline=False)
        embed.add_field(name="Limit", value=f"{data['threshold']} Aktionen", inline=True)
        embed.add_field(name="Zeitfenster", value=f"{data['timeframe']} Sekunden", inline=True)
        embed.add_field(name="Ausgeschlossene Rollen", value=", ".join(excluded_roles) if excluded_roles else "Keine", inline=False)
        
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(AntiRaid(bot))
