import discord
from redbot.core import commands, Config
from datetime import datetime, timedelta
from discord.ext import tasks
import re

class AntiRaid(commands.Cog):
    """Schutz-System und Teamwarn-System."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        default_guild = {
            # Anti-Raid Einstellungen
            "enabled": False,
            "threshold": 3,
            "timeframe": 60,
            "excluded_roles": [],
            
            # Teamwarn Einstellungen
            "warn_channel": None,
            "max_warns": 3,
            "warn_duration": 0,
            "warn_roles": {},
            "team_warnings": {},
            "team_roles": [],
            "warn_allowed_roles": [],
            
            # Panel Einstellungen
            "panel_channel": None,
            "panel_message": None,
            "panel_reverse": False    # NEU: Speichert ob das Panel umgekehrt ist
        }
        
        self.config.register_guild(**default_guild)
        self.action_cache = {}

    def cog_unload(self):
        self.update_panel_loop.cancel()

    async def cog_load(self):
        self.update_panel_loop.start()

    # --- Auto-Update Loop ---

    @tasks.loop(seconds=60)
    async def update_panel_loop(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            panel_channel_id = await self.config.guild(guild).panel_channel()
            panel_message_id = await self.config.guild(guild).panel_message()
            
            if panel_channel_id and panel_message_id:
                channel = guild.get_channel(panel_channel_id)
                if channel:
                    try:
                        message = await channel.fetch_message(panel_message_id)
                        # Prüfe ob Panel reverse ist
                        is_reverse = await self.config.guild(guild).panel_reverse()
                        embeds = await self.generate_teamlist_embeds(guild, reverse=is_reverse)
                        await message.edit(embeds=embeds)
                    except discord.NotFound:
                        await self.config.guild(guild).panel_channel.set(None)
                        await self.config.guild(guild).panel_message.set(None)
                    except (discord.Forbidden, discord.HTTPException):
                        pass

    # --- Anti-Raid System ---

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        if entry.action not in (discord.AuditLogAction.kick, discord.AuditLogAction.ban):
            return

        guild = entry.guild
        mod = entry.user
        
        if mod == guild.me or mod == guild.owner:
            return

        if not await self.config.guild(guild).enabled():
            return

        excluded_role_ids = await self.config.guild(guild).excluded_roles()
        mod_role_ids = [role.id for role in mod.roles]
        if any(role_id in excluded_role_ids for role_id in mod_role_ids):
            return

        if not guild.me.guild_permissions.view_audit_log:
            return

        threshold = await self.config.guild(guild).threshold()
        timeframe = await self.config.guild(guild).timeframe()

        if guild.id not in self.action_cache:
            self.action_cache[guild.id] = {}
        if mod.id not in self.action_cache[guild.id]:
            self.action_cache[guild.id][mod.id] = []

        now = datetime.utcnow()
        
        self.action_cache[guild.id][mod.id] = [
            t for t in self.action_cache[guild.id][mod.id] 
            if now - t < timedelta(seconds=timeframe)
        ]

        self.action_cache[guild.id][mod.id].append(now)

        if len(self.action_cache[guild.id][mod.id]) >= threshold:
            self.action_cache[guild.id][mod.id] = []
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
                pass
            except discord.HTTPException:
                pass

    # --- Teamwarn System ---

    def parse_time(self, time_str):
        match = re.match(r"(\d+)([smhd])", time_str)
        if not match:
            return None
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "s": return amount
        if unit == "m": return amount * 60
        if unit == "h": return amount * 3600
        if unit == "d": return amount * 86400
        return None

    def format_duration(self, seconds):
        if seconds <= 0:
            return "Permanent"
        if seconds < 3600:
            return f"{seconds // 60} Minuten"
        if seconds < 86400:
            return f"{seconds // 3600} Stunden"
        return f"{seconds // 86400} Tage"

    async def update_warn_roles(self, member, guild):
        warnings = await self.config.guild(guild).team_warnings()
        warn_roles_config = await self.config.guild(guild).warn_roles()
        
        if str(member.id) not in warnings:
            return

        now = datetime.utcnow()
        active_warns = []
        for warn in warnings[str(member.id)]:
            if warn['active']:
                if warn['duration'] > 0:
                    warn_time = datetime.fromisoformat(warn['timestamp'])
                    if now - warn_time > timedelta(seconds=warn['duration']):
                        warn['active'] = False
                        continue
                active_warns.append(warn)
        
        await self.config.guild(guild).team_warnings.set(warnings)
        warn_count = len(active_warns)

        roles_to_remove = [guild.get_role(rid) for rid in warn_roles_config.values() if guild.get_role(rid)]
        for role in roles_to_remove:
            if role in member.roles:
                await member.remove_roles(role, reason="Teamwarn Rollen-Update")

        if warn_count > 0 and str(warn_count) in warn_roles_config:
            new_role = guild.get_role(warn_roles_config[str(warn_count)])
            if new_role:
                try:
                    await member.add_roles(new_role, reason=f"Teamwarn Level {warn_count}")
                except discord.Forbidden:
                    pass

    @commands.command()
    async def teamwarn(self, ctx, member: discord.Member, *, args: str = ""):
        """Verwarnt einen Teamler. Optionen: --issuer @User --reason Text --duration 1d"""
        
        if not ctx.author.guild_permissions.manage_guild:
            allowed_ids = await self.config.guild(ctx.guild).warn_allowed_roles()
            if not allowed_ids:
                return await ctx.send("❌ Es wurden keine Rollen berechtigt, Warns auszusprechen.")
            
            author_role_ids = [r.id for r in ctx.author.roles]
            if not any(r_id in allowed_ids for r_id in author_role_ids):
                return await ctx.send("❌ Du hast keine Berechtigung, diesen Befehl zu nutzen.")

        issuer = ctx.author
        reason = "Kein Grund angegeben"
        duration_secs = await self.config.guild(ctx.guild).warn_duration()
        
        if "--issuer" in args:
            match = re.search(r"--issuer\s+<@!?(\d+)>", args)
            if match:
                issuer = ctx.guild.get_member(int(match.group(1))) or issuer
                args = args.replace(match.group(0), "").strip()

        if "--reason" in args:
            match = re.search(r"--reason\s+(.+?)(?=\s+--|$)", args)
            if match:
                reason = match.group(1).strip()
                args = args.replace(match.group(0), "").strip()
        
        if "--duration" in args:
            match = re.search(r"--duration\s+(\w+)", args)
            if match:
                parsed_time = self.parse_time(match.group(1))
                if parsed_time is not None:
                    duration_secs = parsed_time
                args = args.replace(match.group(0), "").strip()

        async with self.config.guild(ctx.guild).team_warnings() as warnings:
            if str(member.id) not in warnings:
                warnings[str(member.id)] = []
            
            warn_data = {
                "reason": reason,
                "issuer_id": issuer.id,
                "timestamp": datetime.utcnow().isoformat(),
                "duration": duration_secs,
                "active": True
            }
            warnings[str(member.id)].append(warn_data)

        await self.update_warn_roles(member, ctx.guild)

        try:
            dm_embed = discord.Embed(
                title=f"⚠️ Teamwarn auf {ctx.guild.name}",
                description=f"Du hast eine Verwarnung erhalten.\n**Grund:** {reason}\n**Aussteller:** {issuer.mention}",
                color=discord.Color.orange()
            )
            if duration_secs > 0:
                dm_embed.add_field(name="Dauer", value=self.format_duration(duration_secs))
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        warn_channel_id = await self.config.guild(ctx.guild).warn_channel()
        if warn_channel_id:
            channel = ctx.guild.get_channel(warn_channel_id)
            if channel:
                chan_embed = discord.Embed(
                    title="🚨 Neuer Teamwarn",
                    color=discord.Color.orange(),
                    timestamp=datetime.utcnow()
                )
                chan_embed.add_field(name="Betroffen", value=f"{member.mention} (`{member.name}`)", inline=False)
                chan_embed.add_field(name="Aussteller", value=f"{issuer.mention} (`{issuer.name}`)", inline=False)
                chan_embed.add_field(name="Grund", value=reason, inline=False)
                chan_embed.add_field(name="Dauer", value=self.format_duration(duration_secs), inline=False)
                await channel.send(embed=chan_embed)

        warnings = await self.config.guild(ctx.guild).team_warnings()
        active_warns = [w for w in warnings.get(str(member.id), []) if w['active']]
        max_warns = await self.config.guild(ctx.guild).max_warns()

        if len(active_warns) >= max_warns:
            try:
                await member.kick(reason=f"Maximale Teamwarns erreicht ({max_warns})")
                await ctx.send(f"🚨 {member.mention} hat die maximale Anzahl an Warns erreicht und wurde gekickt!")
            except discord.Forbidden:
                await ctx.send(f"🚨 {member.mention} hat die maximale Anzahl an Warns erreicht, aber ich habe keine Rechte ihn zu kicken.")
        else:
            await ctx.send(f"✅ {member.mention} wurde erfolgreich verwarnt. (Warn {len(active_warns)}/{max_warns})")

    # --- Teamliste & Panel ---

    async def generate_teamlist_embeds(self, guild, reverse=False):
        team_role_ids = await self.config.guild(guild).team_roles()
        max_warns = await self.config.guild(guild).max_warns()
        all_warnings = await self.config.guild(guild).team_warnings()
        now = datetime.utcnow()

        team_members = []
        
        for member in guild.members:
            highest_team_role = None
            for role in reversed(member.roles):
                if role.id in team_role_ids:
                    highest_team_role = role
                    break
            
            if highest_team_role:
                active_warns = 0
                member_warns = all_warnings.get(str(member.id), [])
                for warn in member_warns:
                    if warn['active']:
                        if warn['duration'] > 0:
                            warn_time = datetime.fromisoformat(warn['timestamp'])
                            if now - warn_time > timedelta(seconds=warn['duration']):
                                continue
                        active_warns += 1

                team_members.append({
                    'member': member,
                    'role': highest_team_role,
                    'warns': active_warns
                })
        
        team_members.sort(key=lambda x: x['role'].position, reverse=True)

        if not team_members:
            return [discord.Embed(title="📋 Teamliste", description="Keine Mitglieder mit den konfigurierten Team-Rollen gefunden.", color=discord.Color.blue())]

        lines = []
        for data in team_members:
            # Hier wird geprüft, ob reverse=True ist
            if reverse:
                line = f"{data['member'].mention} ➜ {data['role'].mention} | Warns: **{data['warns']}/{max_warns}**"
            else:
                line = f"{data['role'].mention} ➜ {data['member'].mention} | Warns: **{data['warns']}/{max_warns}**"
            lines.append(line)
        
        message_chunks = []
        current_chunk = ""
        for line in lines:
            if len(current_chunk) + len(line) + 2 > 1900:
                message_chunks.append(current_chunk)
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"
        
        if current_chunk:
            message_chunks.append(current_chunk)

        embeds = []
        for i, chunk in enumerate(message_chunks):
            embed = discord.Embed(
                title=f"📋 Teamliste (Aktualisiert: <t:{int(now.timestamp())}:R>)" if i == 0 else None,
                description=chunk,
                color=discord.Color.blue()
            )
            embeds.append(embed)
            
        return embeds

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def teamlist(self, ctx):
        """Einstellungen und Befehle für die Teamliste."""
        pass

    @teamlist.command()
    async def show(self, ctx, style: str = "normal"):
        """
        Zeigt die Teamliste einmalig an.
        Nutze 'reverse' für die umgekehrte Ansicht: [p]teamlist show reverse
        """
        is_reverse = style.lower() == "reverse"
        embeds = await self.generate_teamlist_embeds(ctx.guild, reverse=is_reverse)
        for embed in embeds:
            await ctx.send(embed=embed)

    @teamlist.command()
    async def panel(self, ctx, channel: discord.TextChannel, style: str = "normal"):
        """
        Setzt den Channel für das Auto-Update Panel.
        Nutze 'reverse' für die umgekehrte Ansicht: [p]teamlist panel #channel reverse
        """
        is_reverse = style.lower() == "reverse"
        
        # Speichere den Style für den Loop
        await self.config.guild(ctx.guild).panel_reverse.set(is_reverse)
        
        embeds = await self.generate_teamlist_embeds(ctx.guild, reverse=is_reverse)
        message = await channel.send(embeds=embeds)
        
        await self.config.guild(ctx.guild).panel_channel.set(channel.id)
        await self.config.guild(ctx.guild).panel_message.set(message.id)
        
        style_text = "umgekehrten (User ➜ Rolle)" if is_reverse else "normalen (Rolle ➜ User)"
        await ctx.send(f"✅ Teamlist Panel wurde in {channel.mention} erstellt ({style_text} Style). Es aktualisiert sich nun automatisch alle 60 Sekunden.")

    @teamlist.command()
    async def stoppanel(self, ctx):
        """Stoppt das Auto-Update Panel."""
        await self.config.guild(ctx.guild).panel_channel.set(None)
        await self.config.guild(ctx.guild).panel_message.set(None)
        await self.config.guild(ctx.guild).panel_reverse.set(False)
        await ctx.send("✅ Auto-Update Panel wurde gestoppt.")

    # --- Einstellungs-Befehle ---

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def antiraid(self, ctx):
        """Einstellungen für das Anti-Raid & Teamwarn System."""
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
        await ctx.send(f"Anti-Raid Limit wurde auf **{amount}** gesetzt.")

    @antiraid.command()
    async def timeframe(self, ctx, seconds: int):
        """Legt das Anti-Raid Zeitfenster in Sekunden fest (Standard: 60)."""
        if seconds < 5:
            return await ctx.send("Das Zeitfenster muss mindestens 5 Sekunden betragen.")
        await self.config.guild(ctx.guild).timeframe.set(seconds)
        await ctx.send(f"Zeitfenster wurde auf **{seconds}** Sekunden gesetzt.")

    @antiraid.command()
    async def exclude(self, ctx, role: discord.Role):
        """Schließt eine Rolle vom Anti-Raid System aus."""
        if role.is_default():
            return await ctx.send("Du kannst die @everyone Rolle nicht ausschließen!")
        async with self.config.guild(ctx.guild).excluded_roles() as excluded:
            if role.id not in excluded:
                excluded.append(role.id)
                await ctx.send(f"Die Rolle {role.mention} wurde vom Anti-Raid System ausgeschlossen.")
            else:
                await ctx.send(f"Die Rolle {role.mention} ist bereits ausgeschlossen.")

    @antiraid.command()
    async def unexclude(self, ctx, role: discord.Role):
        """Nimmt eine Rolle wieder in das Anti-Raid System auf."""
        async with self.config.guild(ctx.guild).excluded_roles() as excluded:
            if role.id in excluded:
                excluded.remove(role.id)
                await ctx.send(f"Die Rolle {role.mention} wird nun wieder überwacht.")
            else:
                await ctx.send(f"Die Rolle {role.mention} war nicht ausgeschlossen.")

    # --- Teamwarn Einstellungen ---

    @antiraid.group()
    async def warnset(self, ctx):
        """Einstellungen für das Teamwarn-System."""
        pass

    @warnset.command()
    async def channel(self, ctx, channel: discord.TextChannel):
        """Setzt den Channel für die DETAILLIERTEN Warn-Logs."""
        await self.config.guild(ctx.guild).warn_channel.set(channel.id)
        await ctx.send(f"Detaillierter Warn-Log Channel wurde auf {channel.mention} gesetzt.")

    @warnset.command()
    async def maxwarns(self, ctx, amount: int):
        """Setzt die maximale Anzahl an Warns, bevor ein Teamler gekickt wird."""
        if amount < 1:
            return await ctx.send("Die Anzahl muss mindestens 1 sein.")
        await self.config.guild(ctx.guild).max_warns.set(amount)
        await ctx.send(f"Maximale Warns wurden auf **{amount}** gesetzt.")

    @warnset.command()
    async def setduration(self, ctx, time_str: str):
        """Setzt die Standard-Dauer für Warns (z.B. 1d, 12h, 0 für permanent)."""
        if time_str == "0":
            await self.config.guild(ctx.guild).warn_duration.set(0)
            return await ctx.send("Standard-Warndauer ist nun **permanent**.")
        
        secs = self.parse_time(time_str)
        if secs is None:
            return await ctx.send("Ungültiges Format. Nutze z.B. `1d`, `12h`, `30m`.")
        
        await self.config.guild(ctx.guild).warn_duration.set(secs)
        await ctx.send(f"Standard-Warndauer wurde auf **{time_str}** gesetzt.")

    @warnset.command()
    async def setrole(self, ctx, level: int, role: discord.Role):
        """Verknüpft ein Warn-Level mit einer Rolle (z.B. Level 1 = Verwarnung 1)."""
        async with self.config.guild(ctx.guild).warn_roles() as warn_roles:
            warn_roles[str(level)] = role.id
        await ctx.send(f"Warn-Level **{level}** wurde mit der Rolle {role.mention} verknüpft.")

    @warnset.command()
    async def removerole(self, ctx, level: int):
        """Entfernt die Rollen-Verknüpfung für ein Warn-Level."""
        async with self.config.guild(ctx.guild).warn_roles() as warn_roles:
            if str(level) in warn_roles:
                del warn_roles[str(level)]
                await ctx.send(f"Rollen-Verknüpfung für Level **{level}** entfernt.")
            else:
                await ctx.send(f"Für Level **{level}** war keine Rolle verknüpft.")

    @commands.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def teamroleset(self, ctx, *roles: discord.Role):
        """
        Setzt die Rollen, die zum Team gehören (für die Teamliste).
        Nutzung: [p]teamroleset @Admin @Moderator @Supporter
        """
        if not roles:
            return await ctx.send("Bitte gib mindestens eine Rolle an.")
        
        role_ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).team_roles.set(role_ids)
        
        role_mentions = ", ".join([r.mention for r in roles])
        await ctx.send(f"✅ Team-Rollen für die Liste wurden gesetzt auf: {role_mentions}")

    @commands.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def teamwarnroleset(self, ctx, *roles: discord.Role):
        """
        Setzt die Rollen, die den !teamwarn Befehl nutzen dürfen.
        Nutzung: [p]teamwarnroleset @Moderator @Supporter
        """
        if not roles:
            return await ctx.send("Bitte gib mindestens eine Rolle an.")
        
        role_ids = [r.id for r in roles]
        await self.config.guild(ctx.guild).warn_allowed_roles.set(role_ids)
        
        role_mentions = ", ".join([r.mention for r in roles])
        await ctx.send(f"✅ Folgende Rollen dürfen nun den `!teamwarn` Befehl nutzen: {role_mentions}")

    @antiraid.command()
    async def settings(self, ctx):
        """Zeigt die aktuellen Einstellungen an."""
        data = await self.config.guild(ctx.guild).all()
        
        excluded_roles = [f"<@&{rid}>" for rid in data['excluded_roles']]
        embed = discord.Embed(title="Anti-Raid & Teamwarn Einstellungen", color=await ctx.embed_color())
        embed.add_field(name="🛡️ Anti-Raid Aktiviert", value="Ja ✅" if data['enabled'] else "Nein ❌", inline=False)
        embed.add_field(name="🛡️ Raid Limit", value=f"{data['threshold']} Aktionen", inline=True)
        embed.add_field(name="🛡️ Zeitfenster", value=f"{data['timeframe']} Sekunden", inline=True)
        embed.add_field(name="🛡️ Ausgeschlossene Rollen", value=", ".join(excluded_roles) if excluded_roles else "Keine", inline=False)
        
        warn_channel = ctx.guild.get_channel(data['warn_channel'])
        warn_roles_list = [f"Level {lvl}: <@&{rid}>" for lvl, rid in data['warn_roles'].items()]
        team_roles_list = [f"<@&{rid}>" for rid in data['team_roles']]
        warn_allowed_list = [f"<@&{rid}>" for rid in data['warn_allowed_roles']]
        
        panel_channel = ctx.guild.get_channel(data['panel_channel'])
        panel_style = "Umgekehrt (User ➜ Rolle)" if data['panel_reverse'] else "Normal (Rolle ➜ User)"
        
        embed.add_field(name="⚠️ Warn Log Channel", value=warn_channel.mention if warn_channel else "Nicht gesetzt", inline=True)
        embed.add_field(name="⚠️ Max Warns", value=str(data['max_warns']), inline=True)
        embed.add_field(name="⚠️ Warn Rollen", value="\n".join(warn_roles_list) if warn_roles_list else "Keine verknüpft", inline=False)
        embed.add_field(name="📋 Team-Rollen (Liste)", value=", ".join(team_roles_list) if team_roles_list else "Keine gesetzt", inline=False)
        embed.add_field(name="⚔️ Warn-Berechtigte Rollen", value=", ".join(warn_allowed_list) if warn_allowed_list else "Keine gesetzt", inline=False)
        embed.add_field(name="🔄 Auto-Panel", value=f"Aktiv in {panel_channel.mention} ({panel_style})" if panel_channel else "Deaktiviert", inline=False)
        
        await ctx.send(embed=embed)

    # --- Hintergrund-Tasks ---

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for member in guild.members:
                await self.update_warn_roles(member, guild)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.update_warn_roles(member, member.guild)


async def setup(bot):
    await bot.add_cog(AntiRaid(bot))
