import discord
from discord.ext import commands
from redbot.core import Config, checks
import datetime
import asyncio
import re

class SupportSystem(commands.Cog):
    """Ein erweitertes Support-System ähnlich wie bei Galaxy Bot."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        default_guild = {
            "waitroom": None,
            "staff_channel": None,
            "staff_role": None,
            "log_channel": None,
            "blacklist": [],
            "cooldown": 300, # 5 Minuten Standard-Cooldown
            "active_sessions": {}, # Gespeicherte Sessions für Persistenz
            "stats": {} # User ID: {"count": 0, "duration": 0}
        }
        self.config.register_guild(**default_guild)

    async def cog_load(self):
        # Registriere persistente Views beim Laden des Cogs
        self.bot.add_view(SupportClaimView(self))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        guild = member.guild
        if not guild:
            return

        waitroom_id = await self.config.guild(guild).waitroom()
        if not waitroom_id:
            return

        sessions = await self.config.guild(guild).active_sessions()

        # 1. USER BETRITT WARTERAUM
        if after.channel and after.channel.id == waitroom_id:
            # Blacklist prüfen
            blacklist = await self.config.guild(guild).blacklist()
            if member.id in blacklist:
                try:
                    await member.move_to(None, reason="Support Blacklist")
                    await member.send("Du bist auf der Blacklist für das Support-System.")
                except: pass
                return

            # Cooldown prüfen
            cooldown = await self.config.guild(guild).cooldown()
            for msg_id, s_data in sessions.items():
                if s_data.get("user_ids") and member.id in s_data["user_ids"] and s_data.get("end_time"):
                    time_since = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(s_data["end_time"])).total_seconds()
                    if time_since < cooldown:
                        try:
                            await member.move_to(None, reason="Support Cooldown")
                            await member.send(f"Du musst noch {int(cooldown - time_since)} Sekunden warten, bevor du wieder Support anfragen kannst.")
                        except: pass
                        return

            # Nickname ändern (Warteschlangen-Position)
            position = sum(1 for s in sessions.values() if not s.get("end_time") and s.get("status") == "waiting")
            
            original_nick = member.nick if member.nick else member.name
            new_nick = f"[{position}] {original_nick}"[:32] # Discord Limit für Nicknames
            
            try:
                await member.edit(nick=new_nick, reason="Support Warteraum")
            except discord.Forbidden:
                pass # Bot hat keine Rechte dazu

            # Embed & Ping
            staff_channel_id = await self.config.guild(guild).staff_channel()
            staff_channel = guild.get_channel(staff_channel_id)
            if not staff_channel: return

            staff_role_id = await self.config.guild(guild).staff_role()
            staff_role = guild.get_role(staff_role_id) if staff_role_id else None
            ping_content = staff_role.mention if staff_role else "@here"

            embed = discord.Embed(
                title="🔔 Neuer Supportfall",
                description=f"**{member.mention}** benötigt Unterstützung im Warteraum.",
                color=discord.Color.orange(),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(name="👤 Nutzer", value=member.mention, inline=True)
            embed.add_field(name="🆔 ID", value=member.id, inline=True)
            embed.add_field(name="⏱️ Wartezeit", value="<t:0:R>", inline=False) # Wird später aktualisiert
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Supportfall eröffnet")

            view = SupportClaimView(self)
            msg = await staff_channel.send(content=ping_content, embed=embed, view=view)

            # Session in Config speichern
            sessions[str(msg.id)] = {
                "user_ids": [member.id],
                "staff_ids": [],
                "channel_id": None,
                "status": "waiting",
                "start_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "support_start_time": None,
                "original_nicks": {str(member.id): original_nick}
            }
            await self.config.guild(guild).active_sessions.set(sessions)

        # 2. USER VERLÄSST WARTERAUM
        elif before.channel and before.channel.id == waitroom_id:
            # Finde die wartende Session
            session_id = None
            for msg_id, s_data in sessions.items():
                if member.id in s_data["user_ids"] and s_data["status"] == "waiting":
                    session_id = msg_id
                    break
            
            if not session_id: return
            session = sessions[session_id]

            # Nickname zurücksetzen
            orig_nick = session["original_nicks"].get(str(member.id), member.name)
            try:
                await member.edit(nick=orig_nick, reason="Warteraum verlassen")
            except: pass

            # Wurde der User in einen anderen Channel gezogen? (Manuelles Moven)
            if after.channel and after.channel.id != waitroom_id:
                await self.start_support(guild, session_id, after.channel, None, member)
            
            # User hat Voice verlassen
            elif after.channel is None:
                await self.end_session(guild, session_id, "Warteraum verlassen (ohne Support)")

        # 3. USER VERLÄSST AKTIVEN SUPPORT-CHANNEL
        elif before.channel and after.channel is None:
            for msg_id, s_data in sessions.items():
                if s_data["status"] == "active" and before.channel.id == s_data["channel_id"]:
                    if member.id in s_data["user_ids"]:
                        # Prüfen ob noch andere Support-User im Channel sind
                        remaining_users = [u for u in s_data["user_ids"] if u != member.id]
                        if not remaining_users:
                            await self.end_session(guild, msg_id, "User hat den Channel verlassen")
                        else:
                            # User aus der Session entfernen
                            s_data["user_ids"].remove(member.id)
                            sessions[msg_id] = s_data
                            await self.config.guild(guild).active_sessions.set(sessions)
                            await self.update_embed(guild, msg_id, "User verlassen", f"{member.mention} hat den Support verlassen. Restliche User werden weiter unterstützt.")
                    elif member.id in s_data["staff_ids"]:
                        # Teamler verlässt den Channel
                        s_data["staff_ids"].remove(member.id)
                        sessions[msg_id] = s_data
                        await self.config.guild(guild).active_sessions.set(sessions)
                        await self.update_embed(guild, msg_id, "Teamler verlassen", f"{member.mention} hat den Support verlassen.")

        # 4. TEAMLER ODER ZWEITER USER JOINED AKTIVEN SUPPORT CHANNEL (Joint Support)
        elif after.channel and before.channel != after.channel:
            for msg_id, s_data in sessions.items():
                if s_data["status"] == "active" and after.channel.id == s_data["channel_id"]:
                    # Ein Teamler joint (Joint Support)
                    staff_role_id = await self.config.guild(guild).staff_role()
                    if staff_role_id and staff_role_id in [r.id for r in member.roles] and member.id not in s_data["staff_ids"]:
                        s_data["staff_ids"].append(member.id)
                        sessions[msg_id] = s_data
                        await self.config.guild(guild).active_sessions.set(sessions)
                        await self.update_embed(guild, msg_id, "Joint Support", f"{member.mention} unterstützt nun mit.")
                    
                    # Ein weiterer User aus dem Warteraum wird reingezogen (Zusammenhängender Fall)
                    elif member.id in s_data["user_ids"]:
                        pass # Ist bereits in der Session
                    
                    # Jemand anders joint, der noch im Warteraum war?
                    # Prüfen ob der neu gejointe User eine eigene wartende Session hatte
                    for m_id, s_data2 in sessions.items():
                        if s_data2["status"] == "waiting" and member.id in s_data2["user_ids"]:
                            # Wartende Session abbrechen und zum aktiven Support hinzufügen
                            orig_nick = s_data2["original_nicks"].get(str(member.id), member.name)
                            try: await member.edit(nick=orig_nick, reason="Support zusammengelegt")
                            except: pass
                            
                            s_data["user_ids"].append(member.id)
                            s_data["original_nicks"][str(member.id)] = orig_nick
                            sessions[msg_id] = s_data
                            
                            # Alte wartende Session löschen
                            del sessions[m_id]
                            await self.config.guild(guild).active_sessions.set(sessions)
                            
                            # Alte Nachricht updaten
                            try:
                                old_msg = await guild.get_channel(await self.config.guild(guild).staff_channel()).fetch_message(int(m_id))
                                await old_msg.delete()
                            except: pass
                            
                            await self.update_embed(guild, msg_id, "Support zusammengelegt", f"{member.mention} wurde dem Supportfall hinzugefügt.")
                            break

    async def start_support(self, guild, session_id, channel,claimer_id, user):
        sessions = await self.config.guild(guild).active_sessions()
        session = sessions[session_id]
        session["status"] = "active"
        session["channel_id"] = channel.id
        session["support_start_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        if claimer_id:
            session["staff_ids"].append(claimer_id)
        
        sessions[session_id] = session
        await self.config.guild(guild).active_sessions.set(sessions)
        
        claimer_str = f"<@{claimer_id}>" if claimer_id else "Manuell gezogen"
        await self.update_embed(guild, session_id, "✅ Supportfall übernommen", f"Übernommen durch: {claimer_str}\nIn Channel: {channel.mention}")
        
        try:
            await channel.send(f"📣 Support wurde übernommen von {claimer_str}.", delete_after=15)
        except: pass

    async def update_embed(self, guild, session_id, title, description):
        sessions = await self.config.guild(guild).active_sessions()
        if session_id not in sessions: return
        session = sessions[session_id]
        
        staff_channel = guild.get_channel(await self.config.guild(guild).staff_channel())
        if not staff_channel: return
        msg = await staff_channel.fetch_message(int(session_id))
        
        embed = msg.embeds[0]
        embed.color = discord.Color.green() if session["status"] == "active" else discord.Color.red()
        embed.title = title
        
        embed.clear_fields()
        embed.add_field(name="👤 Nutzer", value=", ".join([f"<@{u}>" for u in session["user_ids"]]), inline=True)
        embed.add_field(name="🎧 Teamler", value=", ".join([f"<@{s}>" for s in session["staff_ids"]]) if session["staff_ids"] else "Keiner", inline=True)
        embed.add_field(name="🔊 Channel", value=f"<#{session['channel_id']}>" if session["channel_id"] else "N/A", inline=False)
        embed.add_field(name="ℹ️ Info", value=description, inline=False)
        
        start_time = datetime.datetime.fromisoformat(session["start_time"])
        if session["support_start_time"]:
            s_start = datetime.datetime.fromisoformat(session["support_start_time"])
            embed.add_field(name="⏱️ Wartezeit", value=self.format_timedelta(s_start - start_time), inline=True)
            embed.add_field(name="⏳ Supportzeit", value="<t:0:R>", inline=True) # Wird beim Beenden statisch
        else:
            embed.add_field(name="⏱️ Wartezeit", value="<t:0:R>", inline=True)

        embed.set_footer(text="Support läuft..." if session["status"] == "active" else "Support beendet")
        
        view = SupportCloseView(self, session_id) if session["status"] == "active" else None
        await msg.edit(content=None, embed=embed, view=view)

    async def end_session(self, guild, session_id, reason="Beendet"):
        sessions = await self.config.guild(guild).active_sessions()
        if session_id not in sessions: return
        session = sessions[session_id]
        
        if session["status"] == "ended": return
        
        end_time = datetime.datetime.now(datetime.timezone.utc)
        session["status"] = "ended"
        session["end_time"] = end_time.isoformat()
        
        # Statistiken updaten
        stats = await self.config.guild(guild).stats()
        if session["support_start_time"]:
            s_start = datetime.datetime.fromisoformat(session["support_start_time"])
            duration = (end_time - s_start).total_seconds()
            for s_id in session["staff_ids"]:
                if str(s_id) not in stats: stats[str(s_id)] = {"count": 0, "duration": 0}
                stats[str(s_id)]["count"] += 1
                stats[str(s_id)]["duration"] += duration
            await self.config.guild(guild).stats.set(stats)

        sessions[session_id] = session
        await self.config.guild(guild).active_sessions.set(sessions)
        
        # Channel leeren, falls noch jemand drin ist (Optional: nur User kicken)
        channel = guild.get_channel(session["channel_id"]) if session["channel_id"] else None
        if channel:
            for m in channel.members:
                try: await m.move_to(None, reason="Support beendet")
                except: pass
        
        # End-Embed updaten
        start_time = datetime.datetime.fromisoformat(session["start_time"])
        s_start = datetime.datetime.fromisoformat(session["support_start_time"]) if session["support_start_time"] else end_time
        
        wait_dur = self.format_timedelta(s_start - start_time)
        supp_dur = self.format_timedelta(end_time - s_start)
        
        staff_channel = guild.get_channel(await self.config.guild(guild).staff_channel())
        msg = await staff_channel.fetch_message(int(session_id))
        embed = msg.embeds[0]
        embed.color = discord.Color.red()
        embed.title = "🛑 Supportfall beendet"
        embed.clear_fields()
        embed.add_field(name="👤 Nutzer", value=", ".join([f"<@{u}>" for u in session["user_ids"]]), inline=False)
        embed.add_field(name="🎧 Teamler", value=", ".join([f"<@{s}>" for s in session["staff_ids"]]) if session["staff_ids"] else "Keiner", inline=False)
        embed.add_field(name="⏱️ Wartezeit", value=wait_dur, inline=True)
        embed.add_field(name="⏳ Supportzeit", value=supp_dur, inline=True)
        embed.add_field(name="🚪 Grund", value=reason, inline=False)
        embed.set_footer(text=f"Beendet am {end_time.strftime('%d.%m.%Y %H:%M')}")
        
        await msg.edit(content=None, embed=embed, view=None)
        
        # Log Channel
        log_c_id = await self.config.guild(guild).log_channel()
        if log_c_id:
            log_c = guild.get_channel(log_c_id)
            if log_c:
                await log_c.send(embed=embed)

        # Aus Config löschen nach 24h (oder sofort, hier wir es für Stats/Cooldown kurz behalten)
        # Fürs erste lassen wir es in der Config, der Cooldown greift darauf zu.
        # Um die Config nicht vollzumüllen, könnten wir hier aufräumen, aber für Cooldown brauchen wir es.

    def format_timedelta(self, delta):
        seconds = int(delta.total_seconds())
        periods = [('W', 604800), ('T', 86400), ('h', 3600), ('m', 60), ('s', 1)]
        strings = []
        for period_name, period_seconds in periods:
            if seconds >= period_seconds:
                period_value, seconds = divmod(seconds, period_seconds)
                strings.append(f"{period_value}{period_name}")
        return " ".join(strings) if strings else "0s"

    # --- COMMANDS ---

    @commands.group()
    @checks.admin_or_permissions(manage_guild=True)
    async def supportsetup(self, ctx):
        """Einstellungen für das Support-System."""
        pass

    @supportsetup.command()
    async def waitroom(self, ctx, channel: discord.VoiceChannel):
        """Setzt den Warteraum."""
        await self.config.guild(ctx.guild).waitroom().set(channel.id)
        await ctx.send(f"✅ Warteraum wurde auf {channel.mention} gesetzt.")

    @supportsetup.command()
    async def staffchannel(self, ctx, channel: discord.TextChannel):
        """Setzt den Channel, in dem die Teamler gepingt werden."""
        await self.config.guild(ctx.guild).staff_channel().set(channel.id)
        await ctx.send(f"✅ Staff-Channel wurde auf {channel.mention} gesetzt.")

    @supportsetup.command()
    async def staffrole(self, ctx, role: discord.Role):
        """Setzt die Rolle, die gepingt wird und Support übernehmen darf."""
        await self.config.guild(ctx.guild).staff_role().set(role.id)
        await ctx.send(f"✅ Staff-Rolle wurde auf {role.mention} gesetzt.")

    @supportsetup.command()
    async def logchannel(self, ctx, channel: discord.TextChannel):
        """Setzt einen Log-Channel für beendete Supports."""
        await self.config.guild(ctx.guild).log_channel().set(channel.id)
        await ctx.send(f"✅ Log-Channel wurde auf {channel.mention} gesetzt.")

    @supportsetup.command()
    async def cooldown(self, ctx, seconds: int):
        """Setzt den Cooldown für User nach einem Support (in Sekunden)."""
        await self.config.guild(ctx.guild).cooldown().set(seconds)
        await ctx.send(f"✅ Cooldown auf {seconds} Sekunden gesetzt.")

    @supportsetup.command()
    async def blacklist(self, ctx, user: discord.Member, action: str = None):
        """Fügt einen User zur Blacklist hinzu oder entfernt ihn (add/remove)."""
        bl = await self.config.guild(ctx.guild).blacklist()
        if action == "remove":
            if user.id in bl: bl.remove(user.id)
            await self.config.guild(ctx.guild).blacklist().set(bl)
            await ctx.send(f"✅ {user.mention} wurde von der Blacklist entfernt.")
        else:
            if user.id not in bl: bl.append(user.id)
            await self.config.guild(ctx.guild).blacklist().set(bl)
            await ctx.send(f"✅ {user.mention} wurde zur Blacklist hinzugefügt.")

    @commands.command()
    @checks.mod_or_permissions(manage_messages=True)
    async def supportstats(self, ctx):
        """Zeigt Support-Statistiken der Teamler an."""
        stats = await self.config.guild(ctx.guild).stats()
        if not stats:
            return await ctx.send("Noch keine Statistiken verfügbar.")
            
        embed = discord.Embed(title="📊 Support Statistiken", color=discord.Color.blue())
        sorted_stats = sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True)
        
        text = ""
        for user_id, data in sorted_stats[:10]:
            user = ctx.bot.get_user(int(user_id))
            name = user.name if user else "Unbekannt"
            dur = self.format_timedelta(datetime.timedelta(seconds=data["duration"]))
            text += f"**{name}**: {data['count']} Fälle ({dur} gesamt)\n"
            
        embed.description = text
        await ctx.send(embed=embed)


class SupportClaimView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Support übernehmen", style=discord.ButtonStyle.success, custom_id="support_claim_btn_persistent")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        sessions = await self.cog.config.guild(guild).active_sessions()
        
        session_id = str(interaction.message.id)
        if session_id not in sessions:
            return await interaction.response.send_message("Dieser Supportfall existiert nicht mehr.", ephemeral=True)

        session = sessions[session_id]
        
        # Berechtigungsprüfung
        staff_role_id = await self.cog.config.guild(guild).staff_role()
        if staff_role_id and staff_role_id not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("Du bist nicht berechtigt, Support zu übernehmen.", ephemeral=True)

        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("Du musst dich in einem Voice-Channel befinden, um den Support zu übernehmen.", ephemeral=True)

        if session["status"] == "active":
            return await interaction.response.send_message("Dieser Fall wurde bereits übernommen. Du kannst einfach in den Channel joinen, um zu helfen!", ephemeral=True)

        # User in den Channel des Teamlers moven
        try:
            for u_id in session["user_ids"]:
                member = guild.get_member(u_id)
                if member and member.voice:
                    await member.move_to(interaction.user.voice.channel, reason="Support übernommen")
        except discord.Forbidden:
            return await interaction.response.send_message("Ich habe keine Berechtigung, den Nutzer zu verschieben.", ephemeral=True)

        await self.cog.start_support(guild, session_id, interaction.user.voice.channel, interaction.user.id, None)
        await interaction.response.send_message(f"Du hast den Supportfall übernommen.", ephemeral=True)


class SupportCloseView(discord.ui.View):
    def __init__(self, cog, session_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.session_id = session_id

    @discord.ui.button(label="Support beenden", style=discord.ButtonStyle.danger, custom_id="support_close_btn_persistent")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        sessions = await self.cog.config.guild(guild).active_sessions()
        
        if self.session_id not in sessions:
            return await interaction.response.send_message("Session nicht gefunden.", ephemeral=True)
            
        session = sessions[self.session_id]
        staff_role_id = await self.cog.config.guild(guild).staff_role()
        if staff_role_id and staff_role_id not in [r.id for r in interaction.user.roles]:
             return await interaction.response.send_message("Du bist nicht berechtigt, den Support zu beenden.", ephemeral=True)

        await self.cog.end_session(guild, self.session_id, "Von Teamler beendet")
        await interaction.response.send_message("Support wurde beendet.", ephemeral=True)
