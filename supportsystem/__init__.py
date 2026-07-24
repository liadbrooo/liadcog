import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import datetime
from datetime import timedelta
import re

class SupportSystem(commands.Cog):
    """Ein erweitertes Support-System ähnlich wie bei Galaxy Bot."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        default_guild = {
            "waitroom": None,
            "staff_channel": None,
            "staff_role": None,
            "extra_staff_roles": [],
            "log_channel": None,
            "blacklist": [],
            "cooldown": 300,
            "active_sessions": {},
            "cooldowns": {}, # NEU: Saubere Trennung der Cooldowns
            "stats": {}
        }
        self.config.register_guild(**default_guild)

    async def cog_load(self):
        self.bot.add_view(SupportClaimView(self))
        self.bot.add_view(SupportCloseView(self))

    async def is_staff(self, member: discord.Member):
        guild = member.guild
        staff_role_id = await self.config.guild(guild).staff_role()
        extra_roles = await self.config.guild(guild).extra_staff_roles()
        
        role_ids = [r.id for r in member.roles]
        if staff_role_id and staff_role_id in role_ids:
            return True
        for r_id in extra_roles:
            if r_id in role_ids:
                return True
        return False

    def clean_nick(self, nick: str) -> str:
        """Entfernt vorhandene [X] Wartenummern vom Nickname."""
        if not nick: return ""
        return re.sub(r"^\[\d+\]\s*", "", nick)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        # Ignoriere reine Mute/Deafen Updates
        if before.channel == after.channel:
            return

        guild = member.guild
        if not guild:
            return

        waitroom_id = await self.config.guild(guild).waitroom()
        if not waitroom_id:
            return

        # 1. USER BETRITTT WARTERAUM
        if after.channel and after.channel.id == waitroom_id:
            await self.handle_waitroom_join(member, guild)

        # 2. USER VERLÄSST WARTERAUM
        elif before.channel and before.channel.id == waitroom_id and (not after.channel or after.channel.id != waitroom_id):
            await self.handle_waitroom_leave(member, guild, after.channel)

        # 3. USER WECHSELT CHANNEL (Support verlassen oder in anderen Support gezogen)
        elif before.channel and after.channel and before.channel != after.channel:
            await self.handle_support_leave(member, guild, before.channel)
            await self.handle_support_join(member, guild, after.channel)
            
        # 4. USER DISCONNECTET VOM SUPPORT
        elif before.channel and not after.channel:
            await self.handle_support_leave(member, guild, before.channel)

        # 5. USER JOINED AKTIVEN SUPPORT CHANNEL
        elif after.channel and not before.channel:
            await self.handle_support_join(member, guild, after.channel)

    async def handle_waitroom_join(self, member, guild):
        async with self.config.guild(guild).active_sessions() as sessions:
            for s_data in sessions.values():
                if member.id in s_data.get("user_ids", []) and s_data.get("status") == "waiting":
                    return

        blacklist = await self.config.guild(guild).blacklist()
        if member.id in blacklist:
            try:
                await member.move_to(None, reason="Support Blacklist")
                await member.send("Du bist auf der Blacklist für das Support-System.")
            except: pass
            return

        # Cooldown Check (NEU: Sauber aus dem cooldowns dict)
        async with self.config.guild(guild).cooldowns() as cooldowns:
            if str(member.id) in cooldowns:
                end_time_str = cooldowns[str(member.id)]
                end_time = datetime.datetime.fromisoformat(end_time_str)
                if datetime.datetime.now(datetime.timezone.utc) < end_time:
                    time_left = int((end_time - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
                    try:
                        await member.move_to(None, reason="Support Cooldown")
                        await member.send(f"Du musst noch {time_left} Sekunden warten, bevor du wieder Support anfragen kannst.")
                    except: pass
                    return
                else:
                    del cooldowns[str(member.id)]

        # Nickname und Mute setzen
        async with self.config.guild(guild).active_sessions() as sessions:
            position = sum(1 for s in sessions.values() if s.get("status") == "waiting")
            
            original_nick = self.clean_nick(member.nick if member.nick else member.name)
            if not original_nick: original_nick = member.name
            
            new_nick = f"[{position}] {original_nick}"[:32]
            
            try:
                await member.edit(nick=new_nick, mute=True, reason="Support Warteraum")
            except discord.Forbidden:
                pass

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
            
            ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            embed.add_field(name="⏱️ Wartezeit", value=f"<t:{ts}:R>", inline=False)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Supportfall eröffnet")

            view = SupportClaimView(self)
            allowed_mentions = discord.AllowedMentions(roles=True, everyone=True)
            msg = await staff_channel.send(content=ping_content, embed=embed, view=view, allowed_mentions=allowed_mentions)

            sessions[str(msg.id)] = {
                "user_ids": [member.id],
                "staff_ids": [],
                "channel_id": None,
                "status": "waiting",
                "start_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "support_start_time": None,
                "original_nicks": {str(member.id): original_nick}
            }

    async def handle_waitroom_leave(self, member, guild, after_channel):
        session_id = None
        
        async with self.config.guild(guild).active_sessions() as sessions:
            for msg_id, s_data in sessions.items():
                if member.id in s_data.get("user_ids", []) and s_data.get("status") == "waiting":
                    session_id = msg_id
                    break
            
            if not session_id:
                # Fallback: Nickname aufräumen, falls Session nicht gefunden wurde
                try:
                    clean_name = self.clean_nick(member.nick if member.nick else member.name) or member.name
                    await member.edit(nick=clean_name[:32], mute=False, reason="Warteraum verlassen (Cleanup)")
                except: pass
                return

            orig_nick = sessions[session_id].get("original_nicks", {}).get(str(member.id), member.name)
            try:
                await member.edit(nick=orig_nick, mute=False, reason="Warteraum verlassen")
            except: pass

        if after_channel and after_channel.id != await self.config.guild(guild).waitroom():
            await self.start_support(guild, session_id, after_channel, None)
        else:
            await self.end_session(guild, session_id, "Warteraum verlassen (ohne Support)")

    async def handle_support_leave(self, member, guild, before_channel):
        session_id = None
        
        async with self.config.guild(guild).active_sessions() as sessions:
            for msg_id, s_data in sessions.items():
                if s_data.get("status") == "active" and s_data.get("channel_id") == before_channel.id:
                    session_id = msg_id
                    break
        
        if not session_id: return

        needs_end = False
        update_info = ""
        
        async with self.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions: return
            session = sessions[session_id]
            
            if member.id in session["user_ids"]:
                try: 
                    orig_nick = session["original_nicks"].get(str(member.id), member.name)
                    await member.edit(mute=False, nick=orig_nick, reason="Support verlassen")
                except: pass

                remaining_users = [u for u in session["user_ids"] if u != member.id]
                if not remaining_users:
                    needs_end = True
                else:
                    session["user_ids"].remove(member.id)
                    update_info = f"{member.mention} hat den Support verlassen. Restliche User werden weiter unterstützt."
                    
            elif member.id in session["staff_ids"]:
                session["staff_ids"].remove(member.id)
                update_info = f"{member.mention} hat den Support verlassen."

        if needs_end:
            await self.end_session(guild, session_id, "User hat den Channel verlassen")
        elif update_info:
            await self.update_embed(guild, session_id, "Update", update_info)

    async def handle_support_join(self, member, guild, after_channel):
        is_staff_member = await self.is_staff(member)
        
        active_session_id = None
        waiting_session_id = None
        
        async with self.config.guild(guild).active_sessions() as sessions:
            for msg_id, s_data in sessions.items():
                if s_data.get("status") == "active" and s_data.get("channel_id") == after_channel.id:
                    active_session_id = msg_id
                if s_data.get("status") == "waiting" and member.id in s_data.get("user_ids", []):
                    waiting_session_id = msg_id

        if not active_session_id: return

        do_update = False
        update_title = ""
        update_desc = ""

        async with self.config.guild(guild).active_sessions() as sessions:
            if active_session_id not in sessions: return
            session = sessions[active_session_id]
            
            if is_staff_member and member.id not in session["staff_ids"]:
                session["staff_ids"].append(member.id)
                do_update = True
                update_title = "Joint Support"
                update_desc = f"{member.mention} unterstützt nun mit."
                
            elif waiting_session_id and waiting_session_id != active_session_id:
                if waiting_session_id in sessions:
                    waiting_session = sessions.pop(waiting_session_id)
                    session["user_ids"].append(member.id)
                    session["original_nicks"][str(member.id)] = waiting_session["original_nicks"].get(str(member.id), member.name)
                    
                    try: 
                        orig = session["original_nicks"].get(str(member.id), member.name)
                        await member.edit(mute=False, nick=orig, reason="Support zusammengelegt")
                    except: pass
                    
                    do_update = True
                    update_title = "Support zusammengelegt"
                    update_desc = f"{member.mention} wurde dem Supportfall hinzugefügt."
                
        if do_update:
            if waiting_session_id:
                try:
                    staff_c_id = await self.config.guild(guild).staff_channel()
                    old_msg = await guild.get_channel(staff_c_id).fetch_message(int(waiting_session_id))
                    await old_msg.delete()
                except: pass
            await self.update_embed(guild, active_session_id, update_title, update_desc)

    async def start_support(self, guild, session_id, channel, claimer_id):
        async with self.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions: return
            session = sessions[session_id]
            session["status"] = "active"
            session["channel_id"] = channel.id
            session["support_start_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
            if claimer_id and claimer_id not in session["staff_ids"]:
                session["staff_ids"].append(claimer_id)
        
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
        try:
            msg = await staff_channel.fetch_message(int(session_id))
        except:
            return
            
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
            
            ts = int(s_start.timestamp())
            embed.add_field(name="⏳ Supportzeit", value=f"<t:{ts}:R>", inline=True)
        else:
            ts = int(start_time.timestamp())
            embed.add_field(name="⏱️ Wartezeit", value=f"<t:{ts}:R>", inline=True)

        embed.set_footer(text="Support läuft..." if session["status"] == "active" else "Support beendet")
        
        view = SupportCloseView(self) if session["status"] == "active" else None
        try:
            await msg.edit(content=None, embed=embed, view=view)
        except: pass

    async def end_session(self, guild, session_id, reason="Beendet"):
        user_ids_to_kick = []
        channel_id = None
        end_time = datetime.datetime.now(datetime.timezone.utc)
        
        # NEU: Cooldowns sauber speichern und Session löschen
        async with self.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions: return
            session = sessions[session_id]
            if session["status"] == "ended": return
            
            session["status"] = "ended"
            session["end_time"] = end_time.isoformat()
            
            user_ids_to_kick = list(session["user_ids"])
            channel_id = session["channel_id"]
            original_nicks = session.get("original_nicks", {})
            start_time = datetime.datetime.fromisoformat(session["start_time"])
            s_start = datetime.datetime.fromisoformat(session["support_start_time"]) if session["support_start_time"] else end_time
            staff_ids = session["staff_ids"]
            
            cd_seconds = await self.config.guild(guild).cooldown()
            cd_end_time = end_time + timedelta(seconds=cd_seconds)
            
            async with self.config.guild(guild).cooldowns() as cooldowns:
                for u_id in user_ids_to_kick:
                    cooldowns[str(u_id)] = cd_end_time.isoformat()
                    
            async with self.config.guild(guild).stats() as stats:
                if session["support_start_time"]:
                    duration = (end_time - s_start).total_seconds()
                    for s_id in staff_ids:
                        if str(s_id) not in stats: stats[str(s_id)] = {"count": 0, "duration": 0}
                        stats[str(s_id)]["count"] += 1
                        stats[str(s_id)]["duration"] += duration
            
            # Session endgültig aus active_sessions entfernen (verhindert Config-Müll)
            del sessions[session_id]
        
        # NUR DIE SUPPORTER USER RAUSWERFEN (Teamler bleiben!)
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                for u_id in user_ids_to_kick:
                    m = guild.get_member(u_id)
                    if m and m.voice and m.voice.channel and m.voice.channel.id == channel.id:
                        try: 
                            orig_nick = original_nicks.get(str(m.id), m.name)
                            await m.edit(mute=False, nick=orig_nick, reason="Support beendet")
                            await m.move_to(None, reason="Support beendet")
                        except: pass
        
        wait_dur = self.format_timedelta(s_start - start_time)
        supp_dur = self.format_timedelta(end_time - s_start)
        
        staff_channel = guild.get_channel(await self.config.guild(guild).staff_channel())
        if staff_channel:
            try:
                msg = await staff_channel.fetch_message(int(session_id))
                embed = msg.embeds[0]
                embed.color = discord.Color.red()
                embed.title = "🛑 Supportfall beendet"
                embed.clear_fields()
                embed.add_field(name="👤 Nutzer", value=", ".join([f"<@{u}>" for u in user_ids_to_kick]), inline=False)
                embed.add_field(name="🎧 Teamler", value=", ".join([f"<@{s}>" for s in staff_ids]) if staff_ids else "Keiner", inline=False)
                embed.add_field(name="⏱️ Wartezeit", value=wait_dur, inline=True)
                embed.add_field(name="⏳ Supportzeit", value=supp_dur, inline=True)
                embed.add_field(name="🚪 Grund", value=reason, inline=False)
                embed.set_footer(text=f"Beendet am {end_time.strftime('%d.%m.%Y %H:%M')}")
                
                await msg.edit(content=None, embed=embed, view=None)
                
                log_c_id = await self.config.guild(guild).log_channel()
                if log_c_id:
                    log_c = guild.get_channel(log_c_id)
                    if log_c:
                        await log_c.send(embed=embed)
            except: pass

    def format_timedelta(self, delta):
        seconds = int(delta.total_seconds())
        periods = [('W', 604800), ('T', 86400), ('h', 3600), ('m', 60), ('s', 1)]
        strings = []
        for period_name, period_seconds in periods:
            if seconds >= period_seconds:
                period_value, seconds = divmod(seconds, period_seconds)
                strings.append(f"{period_value}{period_name}")
        return " ".join(strings) if strings else "0s"

    @commands.group(name="lsupportsetup")
    @commands.admin_or_permissions(manage_guild=True)
    async def lsupportsetup(self, ctx: commands.Context):
        """Einstellungen für das Support-System."""
        pass

    @lsupportsetup.command(name="lwaitroom")
    async def lwaitroom(self, ctx: commands.Context, channel: discord.VoiceChannel):
        """Setzt den Warteraum."""
        await self.config.guild(ctx.guild).waitroom.set(channel.id)
        await ctx.send(f"✅ Warteraum wurde auf {channel.mention} gesetzt.")

    @lsupportsetup.command(name="lstaffchannel")
    async def lstaffchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Setzt den Channel, in dem die Teamler gepingt werden."""
        await self.config.guild(ctx.guild).staff_channel.set(channel.id)
        await ctx.send(f"✅ Staff-Channel wurde auf {channel.mention} gesetzt.")

    @lsupportsetup.command(name="lstaffrole")
    async def lstaffrole(self, ctx: commands.Context, role: discord.Role):
        """Setzt die Haupt-Rolle, die GEPINGT wird und Support übernehmen darf."""
        await self.config.guild(ctx.guild).staff_role.set(role.id)
        await ctx.send(f"✅ Haupt-Support-Rolle wurde auf {role.mention} gesetzt.")

    @lsupportsetup.command(name="lextrarole")
    async def lextrarole(self, ctx: commands.Context, role: discord.Role, action: str = "add"):
        """Fügt eine Zusatz-Rolle hinzu, die Support übernehmen darf (OHNE Ping). (add/remove)"""
        extra_roles = await self.config.guild(ctx.guild).extra_staff_roles()
        if action.lower() == "remove":
            if role.id in extra_roles:
                extra_roles.remove(role.id)
                await self.config.guild(ctx.guild).extra_staff_roles.set(extra_roles)
                await ctx.send(f"✅ Zusatz-Rolle {role.mention} wurde entfernt.")
            else:
                await ctx.send("❌ Diese Rolle ist nicht in der Liste der Zusatz-Rollen.")
        else:
            if role.id not in extra_roles:
                extra_roles.append(role.id)
                await self.config.guild(ctx.guild).extra_staff_roles.set(extra_roles)
                await ctx.send(f"✅ Zusatz-Rolle {role.mention} hinzugefügt. Diese kann Support übernehmen, wird aber nicht gepingt.")
            else:
                await ctx.send("❌ Diese Rolle ist bereits eine Zusatz-Rolle.")

    @lsupportsetup.command(name="llogchannel")
    async def llogchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Setzt einen Log-Channel für beendete Supports."""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"✅ Log-Channel wurde auf {channel.mention} gesetzt.")

    @lsupportsetup.command(name="lcooldown")
    async def lcooldown(self, ctx: commands.Context, seconds: int):
        """Setzt den Cooldown für User nach einem Support (in Sekunden)."""
        await self.config.guild(ctx.guild).cooldown.set(seconds)
        await ctx.send(f"✅ Cooldown auf {seconds} Sekunden gesetzt.")

    @lsupportsetup.command(name="lblacklist")
    async def lblacklist(self, ctx: commands.Context, user_id: int, action: str = "add"):
        """Fügt einen User zur Blacklist hinzu oder entfernt ihn (add/remove)."""
        bl = await self.config.guild(ctx.guild).blacklist()
        if action == "remove":
            if user_id in bl: bl.remove(user_id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ Nutzer `{user_id}` wurde von der Blacklist entfernt.")
        else:
            if user_id not in bl: bl.append(user_id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ Nutzer `{user_id}` wurde zur Blacklist hinzugefügt.")

    @lsupportsetup.command(name="lclearsessions")
    async def lclearsessions(self, ctx: commands.Context):
        """NOTFALL: Setzt alle aktiven Support-Sessions zurück und löscht die Warteschlange."""
        await self.config.guild(ctx.guild).active_sessions.set({})
        await self.config.guild(ctx.guild).cooldowns.set({}) # Auch Cooldowns resetten
        await ctx.send("✅ Alle aktiven Support-Sessions und Cooldowns wurden zurückgesetzt.")
        
        waitroom_id = await self.config.guild(ctx.guild).waitroom()
        if waitroom_id:
            waitroom = ctx.guild.get_channel(waitroom_id)
            if waitroom:
                for m in waitroom.members:
                    try:
                        clean_nick = self.clean_nick(m.nick if m.nick else m.name) or m.name
                        await m.edit(mute=False, nick=clean_nick[:32] if clean_nick != m.name else None, reason="Sessions zurückgesetzt")
                    except: pass

    @commands.command(name="lsupportstats")
    @commands.mod_or_permissions(manage_messages=True)
    async def lsupportstats(self, ctx: commands.Context):
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

    @commands.command(name="lsupportinfo")
    @commands.mod_or_permissions(manage_messages=True)
    async def lsupportinfo(self, ctx: commands.Context):
        """Zeigt eine Live-Übersicht aller wartenden und aktiven Supportfälle."""
        sessions = await self.config.guild(ctx.guild).active_sessions()
        
        waiting_users = []
        active_supports = []
        
        for msg_id, s_data in sessions.items():
            if s_data.get("status") == "waiting":
                start_time = datetime.datetime.fromisoformat(s_data["start_time"])
                wait_duration = self.format_timedelta(datetime.datetime.now(datetime.timezone.utc) - start_time)
                users = ", ".join([f"<@{u}>" for u in s_data.get("user_ids", [])])
                waiting_users.append(f"👤 {users} (wartet: {wait_duration})")
                
            elif s_data.get("status") == "active":
                users = ", ".join([f"<@{u}>" for u in s_data.get("user_ids", [])])
                staff = ", ".join([f"<@{s}>" for s in s_data.get("staff_ids", [])]) if s_data.get("staff_ids") else "Unbekannt"
                channel = ctx.guild.get_channel(s_data.get("channel_id", 0))
                chan_name = channel.mention if channel else "Unbekannt"
                active_supports.append(f"🎧 {staff} ➔ {users} ({chan_name})")
        
        embed = discord.Embed(title="📋 Support Live-Übersicht", color=discord.Color.blue(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        
        if waiting_users:
            if len(waiting_users) > 15:
                waiting_text = "\n".join(waiting_users[:15]) + f"\n... und {len(waiting_users) - 15} weitere."
            else:
                waiting_text = "\n".join(waiting_users)
            embed.add_field(name=f"⏳ Im Warteraum ({len(waiting_users)})", value=waiting_text, inline=False)
        else:
            embed.add_field(name="⏳ Im Warteraum", value="Der Warteraum ist aktuell leer. 🎉", inline=False)
            
        if active_supports:
            if len(active_supports) > 15:
                active_text = "\n".join(active_supports[:15]) + f"\n... und {len(active_supports) - 15} weitere."
            else:
                active_text = "\n".join(active_supports)
            embed.add_field(name=f"🎤 Aktiver Support ({len(active_supports)})", value=active_text, inline=False)
        else:
            embed.add_field(name="🎤 Aktiver Support", value="Es finden aktuell keine Supports statt. 🌙", inline=False)
            
        embed.set_footer(text="Live-Status")
        await ctx.send(embed=embed)


class SupportClaimView(discord.ui.View):
    def __init__(self, cog: SupportSystem):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Support übernehmen", style=discord.ButtonStyle.success, custom_id="support_claim_btn_persistent")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        session_id = str(interaction.message.id)
        
        if not await self.cog.is_staff(interaction.user):
            return await interaction.response.send_message("Du bist nicht berechtigt, Support zu übernehmen.", ephemeral=True)

        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("Du musst dich in einem Voice-Channel befinden, um den Support zu übernehmen.", ephemeral=True)

        sessions = await self.cog.config.guild(guild).active_sessions()
        if session_id not in sessions:
            return await interaction.response.send_message("Dieser Supportfall existiert nicht mehr.", ephemeral=True)
        if sessions[session_id]["status"] == "active":
            return await interaction.response.send_message("Dieser Fall wurde bereits übernommen. Du kannst einfach in den Channel joinen, um zu helfen!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        
        target_channel = interaction.user.voice.channel
        moved_any = False
        
        for u_id in sessions[session_id]["user_ids"]:
            member = guild.get_member(u_id)
            if member and member.voice:
                try:
                    await member.move_to(target_channel, reason="Support übernommen")
                    orig_nick = sessions[session_id].get("original_nicks", {}).get(str(member.id), member.name)
                    await member.edit(mute=False, nick=orig_nick, reason="Support übernommen")
                    moved_any = True
                except:
                    pass
                    
        if not moved_any:
            return await interaction.followup.send("Ich konnte keinen Nutzer verschieben (vielleicht haben sie den Voice bereits verlassen?).", ephemeral=True)

        await self.cog.start_support(guild, session_id, target_channel, interaction.user.id)
        await interaction.followup.send("Du hast den Supportfall übernommen.", ephemeral=True)


class SupportCloseView(discord.ui.View):
    def __init__(self, cog: SupportSystem):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Support beenden", style=discord.ButtonStyle.danger, custom_id="support_close_btn_persistent")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        session_id = str(interaction.message.id)
        
        if not await self.cog.is_staff(interaction.user):
            return await interaction.response.send_message("Du bist nicht berechtigt, den Support zu beenden.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        await self.cog.end_session(guild, session_id, "Von Teamler beendet")
        await interaction.followup.send("Support wurde beendet. Der User wurde aus dem Channel entfernt.", ephemeral=True)


async def setup(bot: Red):
    await bot.add_cog(SupportSystem(bot))
