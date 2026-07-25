import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import datetime
from datetime import timedelta
import re
import asyncio

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
            "cooldowns": {},
            "stats": {},
            "user_history": {}
        }
        self.config.register_guild(**default_guild)

    async def cog_load(self):
        self.bot.add_view(SupportClaimView(self))
        self.bot.add_view(SupportControlView(self))

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
        if not nick: return ""
        return re.sub(r"^\[\d+\]\s*", "", nick)

    async def get_mover(self, guild, target_member):
        try:
            await asyncio.sleep(1)
            now = datetime.datetime.now(datetime.timezone.utc)
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_move):
                if (now - entry.created_at).total_seconds() < 15:
                    if entry.target and entry.target.id == target_member.id:
                        if entry.user.id != self.bot.user.id:
                            return entry.user.id
        except discord.Forbidden: pass
        except Exception: pass
        return None

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        if before.channel == after.channel: return

        guild = member.guild
        if not guild: return

        waitroom_id = await self.config.guild(guild).waitroom()
        if not waitroom_id: return

        is_staff_member = await self.is_staff(member)

        if not is_staff_member and after.channel and after.channel.id == waitroom_id:
            await self.handle_waitroom_join(member, guild)
            return

        elif not is_staff_member and before.channel and before.channel.id == waitroom_id and (not after.channel or after.channel.id != waitroom_id):
            await self.handle_waitroom_leave(member, guild, after.channel)
            return

        elif before.channel and after.channel and before.channel != after.channel:
            # FIX: after.channel statt after_channel
            await self.handle_support_leave(member, guild, before.channel)
            await self.handle_support_join(member, guild, after.channel)
            return
            
        elif before.channel and not after.channel:
            await self.handle_support_leave(member, guild, before.channel)
            return

        elif after.channel and not before.channel:
            await self.handle_support_join(member, guild, after.channel)
            return

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

        async with self.config.guild(guild).active_sessions() as sessions:
            position = sum(1 for s in sessions.values() if s.get("status") == "waiting")
            
            original_nick = self.clean_nick(member.nick if member.nick else member.name)
            if not original_nick: original_nick = member.name
            
            new_nick = f"[{position}] {original_nick}"[:32]
            
            try:
                await member.edit(nick=new_nick, mute=True, reason="Support Warteraum")
            except discord.Forbidden: pass

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
            claimer_id = await self.get_mover(guild, member)
            
            active_session_id = None
            async with self.config.guild(guild).active_sessions() as sessions:
                for msg_id, s_data in sessions.items():
                    if s_data.get("status") == "active" and s_data.get("channel_id") == after_channel.id:
                        active_session_id = msg_id
                        break
            
            if active_session_id:
                async with self.config.guild(guild).active_sessions() as sessions:
                    if session_id in sessions and active_session_id in sessions:
                        waiting_session = sessions.pop(session_id)
                        sessions[active_session_id]["user_ids"].append(member.id)
                        sessions[active_session_id]["original_nicks"][str(member.id)] = waiting_session["original_nicks"].get(str(member.id), member.name)
                        
                        if claimer_id and claimer_id not in sessions[active_session_id]["staff_ids"]:
                            mover = guild.get_member(claimer_id)
                            if mover and await self.is_staff(mover):
                                sessions[active_session_id]["staff_ids"].append(claimer_id)
                        
                        for m in after_channel.members:
                            if await self.is_staff(m) and m.id not in sessions[active_session_id]["staff_ids"]:
                                sessions[active_session_id]["staff_ids"].append(m.id)
                
                try:
                    staff_c_id = await self.config.guild(guild).staff_channel()
                    old_msg = await guild.get_channel(staff_c_id).fetch_message(int(session_id))
                    await old_msg.delete()
                except: pass
                
                mover_str = f" Gezogen von: <@{claimer_id}>" if claimer_id else ""
                await self.update_embed(guild, active_session_id, "Support zusammengelegt", f"{member.mention} wurde dem Supportfall hinzugefügt.{mover_str}")
            else:
                await self.start_support(guild, session_id, after_channel, claimer_id)
        else:
            await self.end_session(guild, session_id, "Warteraum verlassen (ohne Support)")

    async def handle_support_leave(self, member, guild, before_channel):
        session_id = None
        
        async with self.config.guild(guild).active_sessions() as sessions:
            for msg_id, s_data in sessions.items():
                if s_data.get("status") in ["active", "paused"] and s_data.get("channel_id") == before_channel.id:
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

                # FIX: Sobald der Support-User weg ist, wird der Fall IMMER beendet. Kein Zusammenführen mehr.
                needs_end = True
                    
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
                if s_data.get("status") in ["active", "paused"] and s_data.get("channel_id") == after_channel.id:
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
            
            # FIX: Komplett entfernt, dass User aus anderen Channels automatisch in den Support gemerged werden.
                
        if do_update:
            if waiting_session_id:
                try:
                    staff_c_id = await self.config.guild(guild).staff_channel()
                    old_msg = await guild.get_channel(staff_c_id).fetch_message(int(waiting_session_id))
                    await old_msg.delete()
                except: pass
            await self.update_embed(guild, active_session_id, update_title, update_desc)

    async def start_support(self, guild, session_id, channel, claimer_id):
        claimer_str = "Manuell gezogen"
        do_update = False
        
        async with self.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions: return
            session = sessions[session_id]
            
            if session["status"] not in ["active", "paused"]:
                session["status"] = "active"
                session["channel_id"] = channel.id
                session["support_start_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                do_update = True
            else:
                if session["channel_id"] != channel.id:
                    session["channel_id"] = channel.id
                    do_update = True
                
            if claimer_id:
                if claimer_id not in session["staff_ids"]:
                    mover = guild.get_member(claimer_id)
                    if mover and await self.is_staff(mover):
                        session["staff_ids"].append(claimer_id)
                        claimer_str = f"<@{claimer_id}>"
                        do_update = True
                else:
                    claimer_str = f"<@{claimer_id}>"
            
            # Fallback: Teamler im Channel scannen
            for m in channel.members:
                if await self.is_staff(m) and m.id not in session["staff_ids"]:
                    session["staff_ids"].append(m.id)
                    do_update = True
                    if claimer_str == "Manuell gezogen":
                        claimer_str = f"<@{m.id}>"
                 
        if do_update:
            await self.update_embed(guild, session_id, "✅ Supportfall übernommen", f"Übernommen durch: {claimer_str}\nIn Channel: {channel.mention}")

    async def update_embed(self, guild, session_id, title, description, view_override=None):
        sessions = await self.config.guild(guild).active_sessions()
        if session_id not in sessions: return
        session = sessions[session_id]
        
        staff_channel = guild.get_channel(await self.config.guild(guild).staff_channel())
        if not staff_channel: return
        try:
            msg = await staff_channel.fetch_message(int(session_id))
        except discord.NotFound:
            async with self.config.guild(guild).active_sessions() as sessions:
                if session_id in sessions: del sessions[session_id]
            return
        except: return
            
        embed = msg.embeds[0]
        
        if "pause" in session.get("status", ""):
            embed.color = discord.Color.yellow()
        elif session["status"] == "active":
            embed.color = discord.Color.green()
        else:
            embed.color = discord.Color.red()
            
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

        embed.set_footer(text="Support läuft..." if session["status"] in ["active", "paused"] else "Support beendet")
        
        view = view_override if view_override else (SupportControlView(self) if session["status"] in ["active", "paused"] else None)
        try:
            await msg.edit(content=None, embed=embed, view=view)
        except: pass

    async def end_session(self, guild, session_id, reason="Beendet", note=None):
        user_ids_to_kick = []
        channel_id = None
        end_time = datetime.datetime.now(datetime.timezone.utc)
        
        async with self.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions: return False
            session = sessions[session_id]
            if session["status"] == "ended": return False
            
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
            
            async with self.config.guild(guild).user_history() as history:
                for u_id in user_ids_to_kick:
                    if str(u_id) not in history:
                        history[str(u_id)] = []
                    history[str(u_id)].append({
                        "end_time": end_time.isoformat(),
                        "duration": (end_time - s_start).total_seconds(),
                        "staff_ids": staff_ids,
                        "reason": reason,
                        "note": note
                    })
                    history[str(u_id)] = history[str(u_id)][-10:]

            del sessions[session_id]
        
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
                
                if note:
                    embed.add_field(name="📝 Notiz", value=note, inline=False)
                
                embed.set_footer(text=f"Beendet am {end_time.strftime('%d.%m.%Y %H:%M')}")
                
                await msg.edit(content=None, embed=embed, view=None)
                
                log_c_id = await self.config.guild(guild).log_channel()
                if log_c_id:
                    log_c = guild.get_channel(log_c_id)
                    if log_c:
                        await log_c.send(embed=embed)
            except: pass
            
        return True

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
        await self.config.guild(ctx.guild).cooldowns.set({})
        await ctx.send("✅ Alle aktiven Support-Sessions und Cooldowns wurden zurückgesetzt.")
        
        for vc in ctx.guild.voice_channels:
            for m in vc.members:
                try:
                    if m.nick and m.nick.startswith("[") and "]" in m.nick:
                        clean_nick = self.clean_nick(m.nick) or m.name
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
                
            elif s_data.get("status") in ["active", "paused"]:
                users = ", ".join([f"<@{u}>" for u in s_data.get("user_ids", [])])
                staff = ", ".join([f"<@{s}>" for s in s_data.get("staff_ids", [])]) if s_data.get("staff_ids") else "Unbekannt"
                channel = ctx.guild.get_channel(s_data.get("channel_id", 0))
                chan_name = channel.mention if channel else "Unbekannt"
                status_emoji = "⏸️" if s_data.get("status") == "paused" else "🎤"
                active_supports.append(f"{status_emoji} {staff} ➔ {users} ({chan_name})")
        
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

    @commands.command(name="lsupportuser")
    @commands.mod_or_permissions(manage_messages=True)
    async def lsupportuser(self, ctx: commands.Context, member: discord.Member):
        """Zeigt die Support-Historie (Akte) eines Users an."""
        history = await self.config.guild(ctx.guild).user_history()
        blacklist = await self.config.guild(ctx.guild).blacklist()
        cooldowns = await self.config.guild(ctx.guild).cooldowns()
        
        user_history = history.get(str(member.id), [])
        
        embed = discord.Embed(title="📁 Support-Akte", color=discord.Color.dark_blue(), timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="👤 Nutzer", value=f"{member.mention} (`{member.id}`)", inline=False)
        
        status_text = ""
        if member.id in blacklist:
            status_text += "🚫 **Gesperrt (Blacklist)**\n"
        
        if str(member.id) in cooldowns:
            end_time = datetime.datetime.fromisoformat(cooldowns[str(member.id)])
            if datetime.datetime.now(datetime.timezone.utc) < end_time:
                time_left = int((end_time - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
                status_text += f"⏳ **Im Cooldown** (noch {time_left}s)\n"
            else:
                status_text += "✅ Nicht im Cooldown\n"
        else:
            status_text += "✅ Nicht im Cooldown\n"
            
        if not status_text:
            status_text = "✅ Keine Einschränkungen"
            
        embed.add_field(name="🛡️ Aktueller Status", value=status_text, inline=False)
        embed.add_field(name="📊 Gesamte Supportfälle", value=str(len(user_history)), inline=False)
        
        if user_history:
            recent_cases = user_history[-3:] 
            cases_text = ""
            for i, case in enumerate(recent_cases, 1):
                end_time = datetime.datetime.fromisoformat(case["end_time"])
                duration_str = self.format_timedelta(datetime.timedelta(seconds=case.get("duration", 0)))
                staff_list = ", ".join([f"<@{s}>" for s in case.get("staff_ids", [])]) or "Unbekannt"
                reason = case.get("reason", "Unbekannt")
                note = case.get("note", "")
                
                cases_text += f"**Fall {i}** ({end_time.strftime('%d.%m.%Y')}):\n"
                cases_text += f"⏱️ Dauer: {duration_str} | 🎧 Teamler: {staff_list}\n"
                cases_text += f"🚪 Grund: {reason}\n"
                if note:
                    cases_text += f"📝 Notiz: {note}\n"
                cases_text += "\n"
                
            embed.add_field(name="📜 Verlauf (Letzte 3)", value=cases_text[:1024], inline=False)
        else:
            embed.add_field(name="📜 Verlauf", value="Dieser User hatte bisher noch keine Supportfälle.", inline=False)
            
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
        if sessions[session_id]["status"] in ["active", "paused"]:
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
                except: pass
                    
        if not moved_any:
            return await interaction.followup.send("Ich konnte keinen Nutzer verschieben (vielleicht haben sie den Voice bereits verlassen?).", ephemeral=True)

        await self.cog.start_support(guild, session_id, target_channel, interaction.user.id)
        await interaction.followup.send("Du hast den Supportfall übernommen.", ephemeral=True)


class SupportControlView(discord.ui.View):
    def __init__(self, cog: SupportSystem):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, custom_id="support_pause_btn_persistent", emoji="⏸️")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        session_id = str(interaction.message.id)
        
        if not await self.cog.is_staff(interaction.user):
            return await interaction.response.send_message("Du bist nicht berechtigt.", ephemeral=True)

        is_paused = False
        msg_text = ""
        
        async with self.cog.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions:
                return await interaction.response.send_message("Session nicht gefunden.", ephemeral=True)
            session = sessions[session_id]
            
            if session["status"] == "active":
                session["status"] = "paused"
                is_paused = True
                msg_text = "Support pausiert (Teamler prüft kurz)."
            elif session["status"] == "paused":
                session["status"] = "active"
                is_paused = False
                msg_text = "Support wird fortgesetzt."
            else:
                return await interaction.response.send_message("Session ist nicht aktiv.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        
        view = SupportControlView(self.cog)
        for child in view.children:
            if child.custom_id == "support_pause_btn_persistent":
                if is_paused:
                    child.label = "Weiter"
                    child.emoji = "▶️"
                else:
                    child.label = "Pause"
                    child.emoji = "⏸️"
                    
        await self.cog.update_embed(guild, session_id, "⏸️ Status Update", msg_text, view_override=view)
        await interaction.followup.send("Status aktualisiert.", ephemeral=True)

    @discord.ui.button(label="Backup rufen", style=discord.ButtonStyle.secondary, custom_id="support_backup_btn_persistent", emoji="🆘")
    async def backup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        session_id = str(interaction.message.id)
        
        if not await self.cog.is_staff(interaction.user):
            return await interaction.response.send_message("Du bist nicht berechtigt, Backup zu rufen.", ephemeral=True)

        sessions = await self.cog.config.guild(guild).active_sessions()
        if session_id not in sessions:
            return await interaction.response.send_message("Dieser Supportfall existiert nicht mehr.", ephemeral=True)
            
        session = sessions[session_id]
        
        staff_channel = guild.get_channel(await self.cog.config.guild(guild).staff_channel())
        if not staff_channel:
            return await interaction.response.send_message("Staff-Channel nicht gefunden.", ephemeral=True)

        staff_role_id = await self.cog.config.guild(guild).staff_role()
        staff_role = guild.get_role(staff_role_id) if staff_role_id else None
        ping_content = staff_role.mention if staff_role else "@here"
        
        users_mention = ", ".join([f"<@{u}>" for u in session.get("user_ids", [])])
        channel_mention = f"<#{session.get('channel_id')}>"
        
        backup_embed = discord.Embed(
            title="🚨 BACKUP ANGEFORDERT!",
            description=f"{interaction.user.mention} braucht dringend Hilfe im Support mit {users_mention}!\nChannel: {channel_mention}",
            color=discord.Color.red()
        )
        
        allowed_mentions = discord.AllowedMentions(roles=True, everyone=True, users=True)
        await staff_channel.send(content=ping_content, embed=backup_embed, allowed_mentions=allowed_mentions)
        
        await interaction.response.send_message("🆘 Backup-Truppe wurde angefordert!", ephemeral=True)

    @discord.ui.button(label="Beenden", style=discord.ButtonStyle.danger, custom_id="support_close_btn_persistent", emoji="🛑")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        session_id = str(interaction.message.id)
        
        if not await self.cog.is_staff(interaction.user):
            return await interaction.response.send_message("Du bist nicht berechtigt, den Support zu beenden.", ephemeral=True)

        sessions = await self.cog.config.guild(guild).active_sessions()
        if session_id not in sessions:
            return await interaction.response.send_message("Dieser Supportfall existiert nicht mehr (wurde evtl. schon beendet).", ephemeral=True)

        modal = CloseNoteModal(self.cog, session_id)
        await interaction.response.send_modal(modal)


class CloseNoteModal(discord.ui.Modal, title="Support beenden - Notiz"):
    def __init__(self, cog: SupportSystem, session_id: str):
        super().__init__()
        self.cog = cog
        self.session_id = session_id

    note_input = discord.ui.TextInput(
        label="Kurze Notiz zum Fall (optional)",
        placeholder="z.B. User hatte Audio-Probleme, Treiber aktualisiert.",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        note = self.note_input.value if self.note_input.value else None
        
        sessions = await self.cog.config.guild(guild).active_sessions()
        if self.session_id not in sessions:
            return await interaction.response.send_message("Dieser Supportfall wurde bereits beendet (z.B. weil der User den Channel verlassen hat).", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        success = await self.cog.end_session(guild, self.session_id, "Von Teamler beendet", note)
        if success:
            await interaction.followup.send("Support wurde beendet. Der User wurde aus dem Channel entfernt.", ephemeral=True)
        else:
            await interaction.followup.send("Fehler beim Beenden des Supportfalls.", ephemeral=True)


async def setup(bot: Red):
    await bot.add_cog(SupportSystem(bot))
