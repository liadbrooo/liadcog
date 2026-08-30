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
        self.cleanup_task = None

    async def cog_load(self):
        self.bot.add_view(SupportClaimView(self))
        self.bot.add_view(SupportControlView(self))
        self.cleanup_task = asyncio.create_task(self.cleanup_orphaned_sessions())

    def cog_unload(self):
        if self.cleanup_task:
            self.cleanup_task.cancel()

    # ------------------------------------------------------------------
    # Hintergrund-Cleanup
    # ------------------------------------------------------------------
    async def cleanup_orphaned_sessions(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)
        while not self.bot.is_closed():
            try:
                await self._cleanup_pass()
            except Exception as e:
                print(f"SupportSystem Cleanup Error: {e}")
            await asyncio.sleep(60)

    async def _cleanup_pass(self):
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            waitroom_id = guild_data.get("waitroom")
            staff_channel = guild.get_channel(guild_data.get("staff_channel"))
            sessions = guild_data.get("active_sessions", {}) or {}

            to_end = []
            to_repost = []

            for session_id, s_data in sessions.items():
                status = s_data.get("status")

                if status in ("active", "paused"):
                    channel_id = s_data.get("channel_id")
                    user_ids = s_data.get("user_ids", [])

                    if not channel_id or not user_ids:
                        to_end.append((session_id, "Daten unvollständig (Auto-Cleanup)"))
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        to_end.append((session_id, "Channel wurde gelöscht"))
                        continue

                    users_present = any(m.id in user_ids for m in channel.members)
                    if not users_present:
                        to_end.append((session_id, "User hat den Channel verlassen"))

                elif status == "waiting":
                    # FIX: Wartende Sessions wurden bisher NIE geprüft. Dadurch
                    # konnten User unsichtbar in der Queue hängen (z.B. wenn die
                    # Anfrage-Nachricht gelöscht wurde oder der Bot offline war).
                    waitroom = guild.get_channel(waitroom_id) if waitroom_id else None
                    if not waitroom:
                        to_end.append((session_id, "Warteraum wurde entfernt"))
                        continue

                    user_present = any(m.id in s_data.get("user_ids", []) for m in waitroom.members)
                    if not user_present:
                        # FIX (Claim-Race): Steckt der User inzwischen in einem
                        # laufenden Support, wird die alte wartende Session
                        # still verworfen. Vorher lief sie auf "User hat den
                        # Warteraum verlassen" hinaus und hängte dem User einen
                        # Cooldown an, während er mitten im Support saß.
                        in_live_support = any(
                            other.get("status") in ("active", "paused")
                            and set(s_data.get("user_ids", [])) & set(other.get("user_ids", []))
                            for oid, other in sessions.items() if oid != session_id
                        )
                        if in_live_support:
                            async with self.config.guild(guild).active_sessions() as live:
                                live.pop(session_id, None)
                            if staff_channel:
                                try:
                                    stale_msg = await staff_channel.fetch_message(int(session_id))
                                    await stale_msg.delete()
                                except Exception:
                                    pass
                            continue
                        to_end.append((session_id, "User hat den Warteraum verlassen"))
                        continue

                    if staff_channel:
                        try:
                            await staff_channel.fetch_message(int(session_id))
                        except discord.NotFound:
                            to_repost.append(session_id)
                        except (ValueError, TypeError):
                            to_end.append((session_id, "Beschädigte Session (Auto-Cleanup)"))
                        except Exception:
                            pass
                    else:
                        to_end.append((session_id, "Staff-Channel fehlt (Auto-Cleanup)"))

            for session_id, reason in to_end:
                try:
                    await self.end_session(guild, session_id, reason)
                except Exception as e:
                    print(f"SupportSystem Cleanup (End) Error: {e}")

            for session_id in to_repost:
                try:
                    await self._repost_waiting_message(guild, session_id)
                except Exception as e:
                    print(f"SupportSystem Cleanup (Repost) Error: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
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
        if not nick:
            return ""
        return re.sub(r"^\[\d+\]\s*", "", nick)

    @staticmethod
    def _is_queue_nick(nick: str) -> bool:
        return bool(nick and nick.startswith("[") and "]" in nick)

    def _capture_original_nick(self, member: discord.Member):
        """FIX: Liest den Original-Nick aus und entfernt hängengebliebene
        Warteschlangen-Präfixe ([12] Name) aus alten, kaputten Sessions.
        Vorher wurde das Präfix als "Original" gespeichert und nach dem
        Support wiederhergestellt."""
        nick = member.nick
        if nick and re.match(r"^\[\d+\]\s*", nick):
            cleaned = self.clean_nick(nick)
            return cleaned if cleaned else None
        return nick

    async def _reset_member_nick(self, member: discord.Member, orig_nick: str = None, is_fallback: bool = False):
        if not member:
            return
        try:
            if is_fallback:
                if self._is_queue_nick(member.nick):
                    clean = self.clean_nick(member.nick)
                    if not clean or clean == member.name:
                        await member.edit(nick=None, reason="Support System Reset (Fallback)")
                    else:
                        await member.edit(nick=clean[:32], reason="Support System Reset (Fallback)")
            else:
                if orig_nick is None:
                    if member.nick is not None:
                        await member.edit(nick=None, reason="Support System Reset (Kein Orig. Nick)")
                else:
                    if member.nick != orig_nick:
                        await member.edit(nick=orig_nick[:32], reason="Support System Reset")
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"SupportSystem Nick Reset Error: {e}")

    async def get_mover(self, guild, target_member):
        try:
            await asyncio.sleep(1)
            now = datetime.datetime.now(datetime.timezone.utc)
            async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.member_move):
                if (now - entry.created_at).total_seconds() < 15:
                    if entry.target and entry.target.id == target_member.id:
                        if entry.user.id != self.bot.user.id:
                            return entry.user.id
        except discord.Forbidden:
            pass
        except Exception:
            pass
        return None

    def format_timedelta(self, delta):
        seconds = int(delta.total_seconds())
        periods = [('W', 604800), ('T', 86400), ('h', 3600), ('m', 60), ('s', 1)]
        strings = []
        for period_name, period_seconds in periods:
            if seconds >= period_seconds:
                period_value, seconds = divmod(seconds, period_seconds)
                strings.append(f"{period_value}{period_name}")
        return " ".join(strings) if strings else "0s"

    # ------------------------------------------------------------------
    # Voice-Events
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        if before.channel == after.channel:
            return

        guild = member.guild
        if not guild:
            return

        waitroom_id = await self.config.guild(guild).waitroom()
        if not waitroom_id:
            return

        is_staff_member = await self.is_staff(member)

        try:
            if not is_staff_member and after.channel and after.channel.id == waitroom_id:
                # Jemand kommt in den Warteraum – auch aus einem laufenden
                # Support heraus. FIX: Die alte Session wird in
                # handle_waitroom_join sauber aufgeräumt.
                await self.handle_waitroom_join(member, guild)
                return

            if not is_staff_member and before.channel and before.channel.id == waitroom_id:
                # Jemand verlässt den Warteraum (gezogen, selbst verschoben
                # oder disconnectet).
                await self.handle_waitroom_leave(member, guild, after.channel)
                return

            # Normale Bewegungen außerhalb des Warteraums
            if before.channel:
                await self.handle_support_leave(member, guild, before.channel, after.channel)
            if after.channel:
                await self.handle_support_join(member, guild, after.channel)
        except Exception as e:
            print(f"SupportSystem VoiceState Error: {e}")

    # ------------------------------------------------------------------
    # Warteraum: Join
    # ------------------------------------------------------------------
    async def _leave_active_session_for_waitroom(self, member, guild):
        """FIX: Wenn ein User aus einem laufenden Support zurück in den
        Warteraum wechselt, muss die alte Session aufgeräumt werden. Vorher
        blieb sie als "Geister-Support" aktiv und wurde nie beendet.
        Ohne Cooldown, sonst würde der User sofort wieder aus dem Warteraum
        geworfen werden."""
        sessions_snapshot = await self.config.guild(guild).active_sessions()
        my_sessions = [
            (sid, s) for sid, s in sessions_snapshot.items()
            if s.get("status") in ("active", "paused") and member.id in s.get("user_ids", [])
        ]
        if not my_sessions:
            return

        for sid, session in my_sessions:
            channel = guild.get_channel(session.get("channel_id"))
            other_users = []
            if channel:
                other_users = [m for m in channel.members
                               if m.id != member.id and m.id in session.get("user_ids", [])]

            orig_nick = session.get("original_nicks", {}).get(str(member.id))

            if other_users:
                # Andere Nutzer sind noch da -> nur diese Person entfernen.
                async with self.config.guild(guild).active_sessions() as sessions:
                    if sid in sessions:
                        s = sessions[sid]
                        if member.id in s.get("user_ids", []):
                            s["user_ids"].remove(member.id)
                        if "original_nicks" in s:
                            s["original_nicks"].pop(str(member.id), None)
                await self._reset_member_nick(member, orig_nick)
                await self.update_embed(guild, sid, "🔄 Nutzerwechsel", f"{member.mention} ist zurück in den Warteraum.")
            else:
                await self.end_session(guild, sid, "User ist zurück in den Warteraum", apply_cooldown=False)
                await self._reset_member_nick(member, orig_nick)

    async def handle_waitroom_join(self, member, guild):
        # 1) FIX: Alte Support-Teilnahme aufräumen (User kam evtl. aus einem
        #    laufenden Support zurück in den Warteraum).
        await self._leave_active_session_for_waitroom(member, guild)

        # 2) Hängt der User schon in der Warteschlange? Dann NICHT doppelt
        #    einreihen, sondern Zustand auffrischen.
        existing_id = None
        async with self.config.guild(guild).active_sessions() as sessions:
            for msg_id, s_data in sessions.items():
                if member.id in s_data.get("user_ids", []) and s_data.get("status") == "waiting":
                    existing_id = msg_id
                    break

        if existing_id:
            await self._ensure_waiting_session(guild, existing_id, member)
            return

        # 3) Blacklist
        blacklist = await self.config.guild(guild).blacklist()
        if member.id in blacklist:
            try:
                await self._reset_member_nick(member, is_fallback=True)
                await member.move_to(None, reason="Support Blacklist")
                await member.send("Du bist auf der Blacklist für das Support-System.")
            except Exception:
                pass
            return

        # 4) Cooldown
        async with self.config.guild(guild).cooldowns() as cooldowns:
            if str(member.id) in cooldowns:
                end_time = None
                try:
                    end_time = datetime.datetime.fromisoformat(cooldowns[str(member.id)])
                except (ValueError, TypeError):
                    cooldowns.pop(str(member.id), None)

                if end_time and datetime.datetime.now(datetime.timezone.utc) < end_time:
                    time_left = int((end_time - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
                    try:
                        await self._reset_member_nick(member, is_fallback=True)
                        await member.move_to(None, reason="Support Cooldown")
                        await member.send(f"Du musst noch {time_left} Sekunden warten, bevor du wieder Support anfragen kannst.")
                    except Exception:
                        pass
                    return
                if end_time:
                    cooldowns.pop(str(member.id), None)

        # 5) FIX: Staff-Channel VOR jeder Mutation prüfen, damit der User
        #    nicht mit kaputtem Nick im Warteraum hängen bleibt
        #    ("erkennt die Leute nicht").
        staff_channel_id = await self.config.guild(guild).staff_channel()
        staff_channel = guild.get_channel(staff_channel_id)
        if not staff_channel:
            return

        # 6) Warteposition berechnen (FIX: 1-basiert statt 0-basiert)
        async with self.config.guild(guild).active_sessions() as sessions:
            position = sum(1 for s in sessions.values() if s.get("status") == "waiting") + 1

        original_nick = self._capture_original_nick(member)
        display_name = original_nick if original_nick else member.name
        new_nick = f"[{position}] {display_name}"

        # 7) MUTE ENTFERNT: Der Bot mutet im Warteraum NICHTS mehr. Sprechen
        #    wird jetzt rein über die Kanalrechte des Warteraum-Channels
        #    geregelt. Das Server-Mute war die Hauptfehlerquelle (Hänger,
        #    überschriebene Mod-Mutes, User wurden nicht erkannt).
        try:
            if member.nick != new_nick[:32]:
                await member.edit(nick=new_nick[:32], reason="Support Warteraum")
        except discord.Forbidden:
            pass
        except Exception:
            pass

        # 8) Anfrage posten (FIX: passiert nicht mehr innerhalb des
        #    Config-Locks -> keine Blockaden bei mehreren gleichzeitigen Joins)
        msg = None
        try:
            msg = await self._send_claim_message(guild, member, position)
        except Exception as e:
            print(f"SupportSystem Claim-Message Error: {e}")

        if not msg:
            # Konnte keine Anfrage posten -> Zustand zurückrollen.
            await self._reset_member_nick(member, is_fallback=True)
            return

        # 9) Session speichern
        async with self.config.guild(guild).active_sessions() as sessions:
            for s_data in sessions.values():
                if member.id in s_data.get("user_ids", []) and s_data.get("status") == "waiting":
                    # Race-Schutz: User wurde in der Zwischenzeit schon
                    # eingereiht -> Zusatz-Nachricht wieder löschen.
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                    return

            sessions[str(msg.id)] = {
                "user_ids": [member.id],
                "staff_ids": [],
                "channel_id": None,
                "status": "waiting",
                "start_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "support_start_time": None,
                "original_nicks": {str(member.id): original_nick}
            }

    # ------------------------------------------------------------------
    # Warteschlangen-Helfer
    # ------------------------------------------------------------------
    async def _waiting_position(self, guild, session_id):
        """Ermittelt die Position einer wartenden Session (1-basiert, nach
        Wartezeit sortiert)."""
        sessions = await self.config.guild(guild).active_sessions()
        waiting = []
        for msg_id, s in sessions.items():
            if s.get("status") == "waiting":
                try:
                    t = datetime.datetime.fromisoformat(s.get("start_time"))
                except (ValueError, TypeError):
                    t = datetime.datetime.now(datetime.timezone.utc)
                waiting.append((t, msg_id))
        waiting.sort(key=lambda x: (x[0], x[1]))
        for idx, (_t, msg_id) in enumerate(waiting, start=1):
            if msg_id == session_id:
                return idx
        return len(waiting) + 1

    async def _build_claim_embed(self, member, position):
        embed = discord.Embed(
            title="🔔 Neuer Supportfall",
            description=f"**{member.mention}** benötigt Unterstützung im Warteraum.",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="👤 Nutzer", value=member.mention, inline=True)
        embed.add_field(name="🆔 ID", value=member.id, inline=True)
        embed.add_field(name="🔢 Position", value=str(position), inline=False)
        ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        embed.add_field(name="⏱️ Wartezeit", value=f"<t:{ts}:R>", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Supportfall eröffnet")
        return embed

    async def _send_claim_message(self, guild, member, position):
        staff_channel_id = await self.config.guild(guild).staff_channel()
        staff_channel = guild.get_channel(staff_channel_id)
        if not staff_channel:
            return None

        staff_role_id = await self.config.guild(guild).staff_role()
        staff_role = guild.get_role(staff_role_id) if staff_role_id else None
        ping_content = staff_role.mention if staff_role else "@here"

        embed = await self._build_claim_embed(member, position)
        view = SupportClaimView(self)
        allowed_mentions = discord.AllowedMentions(roles=True, everyone=True)
        return await staff_channel.send(content=ping_content, embed=embed, view=view, allowed_mentions=allowed_mentions)

    async def _repost_waiting_message(self, guild, session_id):
        """FIX: Postet eine verlorene/gelöschte Anfrage-Nachricht neu und
        verschiebt die Session auf die neue Nachrichten-ID. Vorher blieb der
        User sonst für immer unsichtbar in der Warteschlange hängen.
        Gibt die neue Session-ID zurück (oder None)."""
        sessions_snapshot = await self.config.guild(guild).active_sessions()
        session = sessions_snapshot.get(session_id)
        if not session or session.get("status") != "waiting":
            return None

        user_ids = session.get("user_ids", [])
        if not user_ids:
            return None
        member = guild.get_member(user_ids[0])
        if not member:
            return None

        position = await self._waiting_position(guild, session_id)
        try:
            new_msg = await self._send_claim_message(guild, member, position)
        except Exception as e:
            print(f"SupportSystem Repost Error: {e}")
            return None
        if not new_msg:
            return None

        new_id = str(new_msg.id)
        migrated = False
        async with self.config.guild(guild).active_sessions() as sessions:
            s = sessions.get(session_id)
            if s and s.get("status") == "waiting" and session_id != new_id:
                sessions[new_id] = s
                del sessions[session_id]
                migrated = True

        if migrated:
            # Alte Nachricht defensiv entfernen, falls sie doch noch existiert.
            try:
                staff_channel_id = await self.config.guild(guild).staff_channel()
                staff_channel = guild.get_channel(staff_channel_id)
                if staff_channel:
                    old_msg = await staff_channel.fetch_message(int(session_id))
                    await old_msg.delete()
            except Exception:
                pass
            return new_id

        try:
            await new_msg.delete()
        except Exception:
            pass
        return None

    async def _ensure_waiting_session(self, guild, session_id, member):
        """User hängt schon in der Queue und ist erneut in den Warteraum
        gekommen: Nachricht prüfen/erneuern und Warte-Nick auffrischen
        (z.B. nach Bot-Restart)."""
        try:
            staff_channel_id = await self.config.guild(guild).staff_channel()
            staff_channel = guild.get_channel(staff_channel_id)
            msg_exists = False
            if staff_channel:
                try:
                    await staff_channel.fetch_message(int(session_id))
                    msg_exists = True
                except Exception:
                    msg_exists = False

            target_id = session_id
            if not msg_exists:
                new_id = await self._repost_waiting_message(guild, session_id)
                if new_id:
                    target_id = new_id

            await self._reapply_waiting_state(guild, target_id, member)
        except Exception as e:
            print(f"SupportSystem Waiting-Refresh Error: {e}")

    async def _reapply_waiting_state(self, guild, session_id, member):
        sessions = await self.config.guild(guild).active_sessions()
        session = sessions.get(session_id)
        if not session or session.get("status") != "waiting":
            return

        position = await self._waiting_position(guild, session_id)
        orig_nick = session.get("original_nicks", {}).get(str(member.id))
        display_name = orig_nick if orig_nick else member.name
        new_nick = f"[{position}] {display_name}"

        try:
            if member.nick != new_nick[:32]:
                await member.edit(nick=new_nick[:32], reason="Support Warteraum (Auffrischung)")
        except Exception:
            pass

    async def _renumber_waiting_nicks(self, guild):
        """FIX: Nummeriert die verbleibenden Wartenden neu um, sobald jemand
        aus der Queue entfernt wird (vorher blieben Lücken wie [1], [3], [7])."""
        try:
            waitroom_id = await self.config.guild(guild).waitroom()
            if not waitroom_id:
                return
            waitroom = guild.get_channel(waitroom_id)
            if not waitroom:
                return

            sessions = await self.config.guild(guild).active_sessions()
            waiting = []
            for msg_id, s in sessions.items():
                if s.get("status") == "waiting":
                    try:
                        t = datetime.datetime.fromisoformat(s.get("start_time"))
                    except (ValueError, TypeError):
                        t = datetime.datetime.now(datetime.timezone.utc)
                    waiting.append((t, msg_id, s))
            waiting.sort(key=lambda x: (x[0], x[1]))

            for idx, (_t, _msg_id, s) in enumerate(waiting, start=1):
                for uid in s.get("user_ids", []):
                    m = guild.get_member(uid)
                    if not m or not m.voice or not m.voice.channel or m.voice.channel.id != waitroom_id:
                        continue

                    current = m.nick
                    if self._is_queue_nick(current):
                        display = self.clean_nick(current) or m.name
                    else:
                        display = current or m.name
                    desired = f"[{idx}] {display}"[:32]
                    if current != desired:
                        try:
                            await m.edit(nick=desired, reason="Support Warteraum (Neu sortiert)")
                        except Exception:
                            pass
        except Exception as e:
            print(f"SupportSystem Renumber Error: {e}")

    # ------------------------------------------------------------------
    # Warteraum: Leave
    # ------------------------------------------------------------------
    async def handle_waitroom_leave(self, member, guild, after_channel):
        session_id = None

        async with self.config.guild(guild).active_sessions() as sessions:
            for msg_id, s_data in sessions.items():
                if member.id in s_data.get("user_ids", []) and s_data.get("status") == "waiting":
                    session_id = msg_id
                    break

            if not session_id:
                # Kein Warte-Eintrag. Entweder übernimmt gerade der Claim-Button
                # die Session (Übergangsstatus) oder die Session war kaputt.
                # In beiden Fällen nur den Nick räumen – das Mute übernimmt der
                # Claim-Prozess bzw. die Notfall-Befehle.
                await self._reset_member_nick(member, is_fallback=True)
                return

            orig_nick = sessions[session_id].get("original_nicks", {}).get(str(member.id), None)

        # MUTE ENTFERNT: Der Bot touchiert das Server-Mute nirgends mehr –
        # nur der Warte-Nick wird hier zurückgesetzt.
        await self._reset_member_nick(member, orig_nick)

        waitroom_id = await self.config.guild(guild).waitroom()

        if after_channel and after_channel.id != waitroom_id:
            claimer_id = await self.get_mover(guild, member)

            active_session_id = None
            sessions_snapshot = await self.config.guild(guild).active_sessions()
            for msg_id, s_data in sessions_snapshot.items():
                if msg_id != session_id and s_data.get("status") in ["active", "paused"] \
                        and s_data.get("channel_id") == after_channel.id:
                    active_session_id = msg_id
                    break

            if active_session_id:
                await self._merge_waiting_into_active(guild, session_id, active_session_id, member, claimer_id, after_channel)
            else:
                # FIX: Nur wenn ein Teamler gezogen hat ODER ein Teamler im
                # Ziel-Channel sitzt, wird daraus ein Support. Zieht sich der
                # User selbst in einen beliebigen Channel, verlässt er stattdessen
                # sauber die Warteschlange (vorher entstanden "Phantom-Supports").
                mover_is_staff = False
                if claimer_id:
                    mover = guild.get_member(claimer_id)
                    mover_is_staff = bool(mover and await self.is_staff(mover))

                staff_present = False
                for m in after_channel.members:
                    if await self.is_staff(m):
                        staff_present = True
                        break

                if mover_is_staff or staff_present:
                    await self.start_support(guild, session_id, after_channel, claimer_id)
                else:
                    await self.end_session(guild, session_id, "Warteraum verlassen (ohne Support)")
        else:
            # User hat den Warteraum komplett verlassen (Disconnect).
            await self.end_session(guild, session_id, "Warteraum verlassen")

        # FIX: Warteschlange neu nummerieren.
        await self._renumber_waiting_nicks(guild)

    async def _merge_waiting_into_active(self, guild, waiting_session_id, active_session_id, member, claimer_id, target_channel):
        async with self.config.guild(guild).active_sessions() as sessions:
            if waiting_session_id not in sessions or active_session_id not in sessions:
                return
            waiting = sessions.pop(waiting_session_id)
            active = sessions[active_session_id]

            if member.id not in active.get("user_ids", []):
                active.setdefault("user_ids", []).append(member.id)
            active.setdefault("original_nicks", {})[str(member.id)] = waiting.get("original_nicks", {}).get(str(member.id), None)

            if claimer_id:
                mover = guild.get_member(claimer_id)
                if mover and await self.is_staff(mover) and claimer_id not in active.get("staff_ids", []):
                    active.setdefault("staff_ids", []).append(claimer_id)

            for m in target_channel.members:
                if await self.is_staff(m) and m.id not in active.get("staff_ids", []):
                    active.setdefault("staff_ids", []).append(m.id)

        # Alte Warte-Anfrage löschen
        try:
            staff_c_id = await self.config.guild(guild).staff_channel()
            staff_channel = guild.get_channel(staff_c_id)
            if staff_channel:
                old_msg = await staff_channel.fetch_message(int(waiting_session_id))
                await old_msg.delete()
        except Exception:
            pass

        mover_str = f" Gezogen von: <@{claimer_id}>" if claimer_id else ""
        await self.update_embed(guild, active_session_id, "Support zusammengelegt",
                                f"{member.mention} wurde dem Supportfall hinzugefügt.{mover_str}")

    # ------------------------------------------------------------------
    # Support-Channel: Leave
    # ------------------------------------------------------------------
    async def handle_support_leave(self, member, guild, before_channel, after_channel=None):
        sessions_snapshot = await self.config.guild(guild).active_sessions()
        session_id = None
        for msg_id, s_data in sessions_snapshot.items():
            if s_data.get("status") in ["active", "paused"] and s_data.get("channel_id") == before_channel.id:
                session_id = msg_id
                break

        if not session_id:
            return

        session = sessions_snapshot.get(session_id, {})
        is_session_user = member.id in session.get("user_ids", [])
        is_session_staff = member.id in session.get("staff_ids", [])

        if not is_session_user and not is_session_staff:
            return

        if is_session_user:
            orig_nick = session.get("original_nicks", {}).get(str(member.id), None)

            # FIX: Wird der User in einen ANDEREN aktiven Support-Channel gezogen
            # (oder wechselt selbst dorthin), wird zusammengeführt statt der
            # gesamte Fall beendet.
            if after_channel is not None:
                target_id = None
                for msg_id, s_data in sessions_snapshot.items():
                    if msg_id != session_id and s_data.get("status") in ["active", "paused"] \
                            and s_data.get("channel_id") == after_channel.id:
                        target_id = msg_id
                        break
                if target_id:
                    await self._merge_running_user(guild, session_id, target_id, member)
                    return

            # FIX: KRITISCHER BUG – vorher wurde der GESAMTE Support beendet,
            # sobald EIN (auch zusammengelegter) User den Channel verließ, und
            # alle anderen wurden per move_to(None) rausgeworfen ("schmeißt sie
            # mitten im Support raus"). Jetzt zählt: Sind noch andere Nutzer im
            # Channel, bleibt der Support bestehen.
            remaining = [m for m in before_channel.members
                         if m.id != member.id and m.id in session.get("user_ids", [])]

            await self._reset_member_nick(member, orig_nick)

            if remaining:
                async with self.config.guild(guild).active_sessions() as sessions:
                    if session_id in sessions:
                        s = sessions[session_id]
                        if member.id in s.get("user_ids", []):
                            s["user_ids"].remove(member.id)
                        if "original_nicks" in s:
                            s["original_nicks"].pop(str(member.id), None)
                await self.update_embed(guild, session_id, "Update", f"{member.mention} hat den Support verlassen.")
            else:
                await self.end_session(guild, session_id, "User hat den Channel verlassen")

        elif is_session_staff:
            async with self.config.guild(guild).active_sessions() as sessions:
                if session_id in sessions and member.id in sessions[session_id].get("staff_ids", []):
                    sessions[session_id]["staff_ids"].remove(member.id)
            await self.update_embed(guild, session_id, "Update", f"{member.mention} hat den Support verlassen.")

    async def _merge_running_user(self, guild, from_session_id, to_session_id, member):
        """Führt einen laufenden Supportfall in einen anderen zusammen
        (User wurde in einen anderen Support-Channel bewegt)."""
        async with self.config.guild(guild).active_sessions() as sessions:
            if from_session_id not in sessions or to_session_id not in sessions:
                return
            source = sessions.pop(from_session_id)
            target = sessions[to_session_id]

            # FIX: Vorher wurde nur der WECHSELNDE User übernommen. Saßen im
            # alten Fall weitere Nutzer, wurden diese mit dem kompletten Fall
            # verworfen und saßen danach los im Channel (kein Case, kein
            # Beenden möglich). Jetzt wandert der ganze Fall mit.
            for u_id in source.get("user_ids", []):
                if u_id not in target.get("user_ids", []):
                    target.setdefault("user_ids", []).append(u_id)
            for uid_str, nick in source.get("original_nicks", {}).items():
                target.setdefault("original_nicks", {}).setdefault(uid_str, nick)
            for sid in source.get("staff_ids", []):
                if sid not in target.get("staff_ids", []):
                    target.setdefault("staff_ids", []).append(sid)

        try:
            staff_c_id = await self.config.guild(guild).staff_channel()
            staff_channel = guild.get_channel(staff_c_id)
            if staff_channel:
                old_msg = await staff_channel.fetch_message(int(from_session_id))
                await old_msg.delete()
        except Exception:
            pass

        await self.update_embed(guild, to_session_id, "Support zusammengelegt",
                                f"{member.mention} wurde dem Supportfall hinzugefügt.")

    # ------------------------------------------------------------------
    # Support-Channel: Join
    # ------------------------------------------------------------------
    async def handle_support_join(self, member, guild, after_channel):
        is_staff_member = await self.is_staff(member)

        active_session_id = None
        waiting_session_id = None

        sessions_snapshot = await self.config.guild(guild).active_sessions()
        for msg_id, s_data in sessions_snapshot.items():
            if s_data.get("status") in ["active", "paused"] and s_data.get("channel_id") == after_channel.id:
                active_session_id = msg_id
            if s_data.get("status") == "waiting" and member.id in s_data.get("user_ids", []):
                waiting_session_id = msg_id

        if not active_session_id:
            return

        do_update = False
        update_title = ""
        update_desc = ""
        merged_waiting = None

        async with self.config.guild(guild).active_sessions() as sessions:
            if active_session_id not in sessions:
                return
            session = sessions[active_session_id]

            if is_staff_member and member.id not in session.get("staff_ids", []):
                session.setdefault("staff_ids", []).append(member.id)
                do_update = True
                update_title = "Joint Support"
                update_desc = f"{member.mention} unterstützt nun mit."

            elif waiting_session_id and waiting_session_id != active_session_id \
                    and member.id not in session.get("user_ids", []):
                if waiting_session_id in sessions:
                    waiting_session = sessions.pop(waiting_session_id)
                    session.setdefault("user_ids", []).append(member.id)
                    orig_nick = waiting_session.get("original_nicks", {}).get(str(member.id), None)
                    session.setdefault("original_nicks", {})[str(member.id)] = orig_nick

                    await self._reset_member_nick(member, orig_nick)

                    do_update = True
                    update_title = "Support zusammengelegt"
                    update_desc = f"{member.mention} wurde dem Supportfall hinzugefügt."
                    merged_waiting = waiting_session_id

        if do_update:
            if merged_waiting:
                try:
                    staff_c_id = await self.config.guild(guild).staff_channel()
                    staff_channel = guild.get_channel(staff_c_id)
                    if staff_channel:
                        old_msg = await staff_channel.fetch_message(int(merged_waiting))
                        await old_msg.delete()
                except Exception:
                    pass
            await self.update_embed(guild, active_session_id, update_title, update_desc)

    # ------------------------------------------------------------------
    # Support starten / Embeds / Ende
    # ------------------------------------------------------------------
    async def start_support(self, guild, session_id, channel, claimer_id):
        # FIX: Staff-Scan außerhalb des Config-Locks (vorher wurden pro Member
        # zwei Config-Reads IM Lock gemacht -> Blockaden bei Voice-Events).
        staff_in_channel = []
        for m in channel.members:
            try:
                if await self.is_staff(m):
                    staff_in_channel.append(m.id)
            except Exception:
                pass

        do_update = False
        claimer_str = "Manuell gezogen"

        async with self.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions:
                return
            session = sessions[session_id]

            if session.get("status") in ("active", "paused"):
                # Läuft bereits. FIX: Der Channel wird NICHT mehr umgebogen –
                # vorher sprang der Fall bei einer Doppel-Übernahme in den
                # anderen Channel und der User war "verloren".
                if claimer_id and claimer_id not in session.get("staff_ids", []):
                    mover = guild.get_member(claimer_id)
                    if mover and await self.is_staff(mover):
                        session.setdefault("staff_ids", []).append(claimer_id)
                        do_update = True
                for sid in staff_in_channel:
                    if sid not in session.get("staff_ids", []):
                        session.setdefault("staff_ids", []).append(sid)
                        do_update = True
            else:
                session["status"] = "active"
                session["channel_id"] = channel.id
                session["support_start_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                do_update = True

                if claimer_id:
                    mover = guild.get_member(claimer_id)
                    if mover and await self.is_staff(mover):
                        if claimer_id not in session.get("staff_ids", []):
                            session.setdefault("staff_ids", []).append(claimer_id)
                        claimer_str = f"<@{claimer_id}>"

                for sid in staff_in_channel:
                    if sid not in session.get("staff_ids", []):
                        session.setdefault("staff_ids", []).append(sid)
                        if claimer_str == "Manuell gezogen":
                            claimer_str = f"<@{sid}>"

        if do_update:
            await self.update_embed(guild, session_id, "✅ Supportfall übernommen",
                                    f"Übernommen durch: {claimer_str}\nIn Channel: {channel.mention}")

    async def update_embed(self, guild, session_id, title, description, view_override=None):
        sessions = await self.config.guild(guild).active_sessions()
        if session_id not in sessions:
            return
        session = sessions[session_id]

        staff_channel = guild.get_channel(await self.config.guild(guild).staff_channel())
        if not staff_channel:
            return
        try:
            msg = await staff_channel.fetch_message(int(session_id))
        except discord.NotFound:
            # FIX: "Nachricht gelöscht" ist NICHT "Support beendet". Vorher hat
            # der Cog hier die Session einfach weggeworfen -> der Support ließ
            # sich nicht mehr sauber beenden (Cooldown, Kick, Log fehlten).
            print("SupportSystem: Staff-Nachricht fehlt (Update übersprungen).")
            return
        except Exception as e:
            print(f"SupportSystem Fetch Error (Update): {e}")
            return

        if not msg.embeds:
            return
        embed = msg.embeds[0]

        status = session.get("status", "")
        if "pause" in status:
            embed.color = discord.Color.yellow()
        elif status == "active":
            embed.color = discord.Color.green()
        else:
            embed.color = discord.Color.red()

        embed.title = title

        embed.clear_fields()
        embed.add_field(name="👤 Nutzer", value=", ".join([f"<@{u}>" for u in session.get("user_ids", [])]) or "Keiner", inline=True)
        embed.add_field(name="🎧 Teamler", value=", ".join([f"<@{s}>" for s in session.get("staff_ids", [])]) if session.get("staff_ids") else "Keiner", inline=True)
        embed.add_field(name="🔊 Channel", value=f"<#{session['channel_id']}>" if session.get("channel_id") else "N/A", inline=False)
        embed.add_field(name="ℹ️ Info", value=description, inline=False)

        try:
            start_time = datetime.datetime.fromisoformat(session.get("start_time"))
        except (ValueError, TypeError):
            start_time = datetime.datetime.now(datetime.timezone.utc)

        if session.get("support_start_time"):
            try:
                s_start = datetime.datetime.fromisoformat(session["support_start_time"])
            except (ValueError, TypeError):
                s_start = start_time
            embed.add_field(name="⏱️ Wartezeit", value=self.format_timedelta(s_start - start_time), inline=True)
            ts = int(s_start.timestamp())
            embed.add_field(name="⏳ Supportzeit", value=f"<t:{ts}:R>", inline=True)
        else:
            ts = int(start_time.timestamp())
            embed.add_field(name="⏱️ Wartezeit", value=f"<t:{ts}:R>", inline=True)

        embed.set_footer(text="Support läuft..." if status in ["active", "paused"] else "Support beendet")

        view = view_override if view_override else (SupportControlView(self) if status in ["active", "paused"] else None)
        try:
            await msg.edit(content=None, embed=embed, view=view)
        except Exception as e:
            print(f"SupportSystem Edit Error (Update): {e}")

    async def _force_close_embed(self, guild, session_id, reason="Beendet", note=None):
        staff_channel = guild.get_channel(await self.config.guild(guild).staff_channel())
        if not staff_channel:
            return
        try:
            msg = await staff_channel.fetch_message(int(session_id))
            if not msg.embeds:
                return
            embed = msg.embeds[0]
            embed.color = discord.Color.red()
            embed.title = "🛑 Supportfall beendet"
            embed.clear_fields()
            embed.add_field(name="ℹ️ Info", value="Support wurde im Hintergrund bereits geschlossen.", inline=False)
            embed.add_field(name="🚪 Grund", value=reason, inline=False)
            if note:
                embed.add_field(name="📝 Notiz", value=note, inline=False)
            embed.set_footer(text="Beendet (Zwangs-Close)")
            await msg.edit(content=None, embed=embed, view=None)
        except Exception as e:
            print(f"SupportSystem Force Close Error: {e}")

    async def end_session(self, guild, session_id, reason="Beendet", note=None, apply_cooldown=True):
        user_ids_to_kick = []
        staff_ids = []
        original_nicks = {}
        channel_id = None
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time
        s_start = end_time

        async with self.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions:
                await self._force_close_embed(guild, session_id, reason, note)
                return False
            session = sessions[session_id]
            if session.get("status") == "ended":
                return False

            user_ids_to_kick = list(session.get("user_ids", []))
            staff_ids = list(session.get("staff_ids", []))
            channel_id = session.get("channel_id")
            original_nicks = session.get("original_nicks", {})

            try:
                start_time = datetime.datetime.fromisoformat(session.get("start_time"))
            except (ValueError, TypeError):
                start_time = end_time
            if session.get("support_start_time"):
                try:
                    s_start = datetime.datetime.fromisoformat(session["support_start_time"])
                except (ValueError, TypeError):
                    s_start = end_time
            else:
                s_start = end_time

            # FIX: Cooldown ist jetzt abschaltbar. Wer aus dem laufenden Support
            # zurück in den Warteraum wechselt, bekommt keinen Cooldown
            # (sonst würde er sofort wieder rausgeschmissen).
            if apply_cooldown:
                cd_seconds = await self.config.guild(guild).cooldown()
                cd_end_time = end_time + timedelta(seconds=cd_seconds)
                async with self.config.guild(guild).cooldowns() as cooldowns:
                    for u_id in user_ids_to_kick:
                        cooldowns[str(u_id)] = cd_end_time.isoformat()

            if session.get("support_start_time"):
                async with self.config.guild(guild).stats() as stats:
                    duration = (end_time - s_start).total_seconds()
                    for s_id in staff_ids:
                        if str(s_id) not in stats:
                            stats[str(s_id)] = {"count": 0, "duration": 0}
                        stats[str(s_id)]["count"] += 1
                        stats[str(s_id)]["duration"] += duration

            async with self.config.guild(guild).user_history() as history:
                for u_id in user_ids_to_kick:
                    history.setdefault(str(u_id), [])
                    history[str(u_id)].append({
                        "end_time": end_time.isoformat(),
                        "duration": (end_time - s_start).total_seconds(),
                        "staff_ids": staff_ids,
                        "reason": reason,
                        "note": note
                    })
                    history[str(u_id)] = history[str(u_id)][-10:]

            # WICHTIG: Session löschen, BEVOR rausgeschoben wird – dadurch
            # können die dadurch ausgelösten Voice-Events nicht rekursiv
            # wieder in die Session-Verarbeitung laufen.
            del sessions[session_id]

        for u_id in user_ids_to_kick:
            m = guild.get_member(u_id)
            if not m:
                continue
            await self._reset_member_nick(m, original_nicks.get(str(m.id), None))
            if m.voice and m.voice.channel:
                in_support_channel = bool(channel_id and m.voice.channel.id == channel_id)

                if in_support_channel:
                    try:
                        # MUTE ENTFERNT: Der Bot mutet nicht mehr und muss
                        # deshalb auch nichts mehr entmuten. Der User wird
                        # nur sauber aus dem Support-Channel geholt.
                        await m.move_to(None, reason="Support beendet")
                    except Exception:
                        pass

        wait_dur = self.format_timedelta(s_start - start_time)
        supp_dur = self.format_timedelta(end_time - s_start)

        staff_channel = guild.get_channel(await self.config.guild(guild).staff_channel())
        if staff_channel:
            try:
                msg = await staff_channel.fetch_message(int(session_id))
                if msg.embeds:
                    embed = msg.embeds[0]
                    embed.color = discord.Color.red()
                    embed.title = "🛑 Supportfall beendet"
                    embed.clear_fields()
                    embed.add_field(name="👤 Nutzer", value=", ".join([f"<@{u}>" for u in user_ids_to_kick]) or "Keiner", inline=False)
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
            except Exception as e:
                print(f"SupportSystem Edit Error (End): {e}")

        return True

    # ------------------------------------------------------------------
    # Setup-Befehle
    # ------------------------------------------------------------------
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
            if user_id in bl:
                bl.remove(user_id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ Nutzer `{user_id}` wurde von der Blacklist entfernt.")
        else:
            if user_id not in bl:
                bl.append(user_id)
            await self.config.guild(ctx.guild).blacklist.set(bl)
            await ctx.send(f"✅ Nutzer `{user_id}` wurde zur Blacklist hinzugefügt.")

    @lsupportsetup.command(name="lclearsessions")
    async def lclearsessions(self, ctx: commands.Context):
        """NOTFALL: Setzt alle Support-Sessions zurück, löscht Warteschlange und reinigt alle Nicknames."""
        await self.config.guild(ctx.guild).active_sessions.set({})
        await self.config.guild(ctx.guild).cooldowns.set({})

        cleaned_count = 0
        for vc in ctx.guild.voice_channels:
            for m in vc.members:
                if self._is_queue_nick(m.nick):
                    await self._reset_member_nick(m, is_fallback=True)
                    cleaned_count += 1

        await ctx.send(f"✅ Alle Support-Sessions zurückgesetzt. {cleaned_count} Nicknames wurden gereinigt.")

    @lsupportsetup.command(name="lsettings")
    async def lsettings(self, ctx: commands.Context):
        """Zeigt alle aktuellen Einstellungen des Support-Systems an."""
        data = await self.config.guild(ctx.guild).all()

        waitroom = ctx.guild.get_channel(data.get("waitroom")) if data.get("waitroom") else None
        staff_c = ctx.guild.get_channel(data.get("staff_channel")) if data.get("staff_channel") else None
        log_c = ctx.guild.get_channel(data.get("log_channel")) if data.get("log_channel") else None
        staff_r = ctx.guild.get_role(data.get("staff_role")) if data.get("staff_role") else None
        extra_rs = [ctx.guild.get_role(r).mention for r in data.get("extra_staff_roles", []) if ctx.guild.get_role(r)]

        embed = discord.Embed(title="⚙️ Support System Einstellungen", color=discord.Color.dark_blue(), timestamp=datetime.datetime.now(datetime.timezone.utc))

        embed.add_field(name="🔊 Warteraum", value=waitroom.mention if waitroom else "❌ Nicht gesetzt", inline=False)
        embed.add_field(name="📋 Staff-Channel", value=staff_c.mention if staff_c else "❌ Nicht gesetzt", inline=False)
        embed.add_field(name="📡 Log-Channel", value=log_c.mention if log_c else "❌ Nicht gesetzt", inline=False)
        embed.add_field(name="👑 Haupt-Support-Rolle", value=staff_r.mention if staff_r else "❌ Nicht gesetzt", inline=False)
        embed.add_field(name="👥 Zusatz-Rollen (ohne Ping)", value=", ".join(extra_rs) if extra_rs else "Keine gesetzt", inline=False)
        embed.add_field(name="⏱️ Cooldown", value=f"{data.get('cooldown', 300)} Sekunden", inline=False)

        await ctx.send(embed=embed)

    @lsupportsetup.command(name="lforceclose")
    async def lforceclose(self, ctx: commands.Context, member: discord.Member):
        """Zwingt einen User sofort aus dem Support/Warteraum (Notfall-Kick)."""
        sessions = await self.config.guild(ctx.guild).active_sessions()
        session_id_to_close = None

        for msg_id, s_data in sessions.items():
            if member.id in s_data.get("user_ids", []):
                session_id_to_close = msg_id
                break

        if not session_id_to_close:
            return await ctx.send(f"❌ {member.mention} befindet sich in keinem aktiven oder wartenden Supportfall.")

        await ctx.send(f"🔧 Schließe Supportfall für {member.mention} erzwungen...")
        success = await self.end_session(ctx.guild, session_id_to_close, "Von Admin zwangsgeschlossen")

        if success:
            await ctx.send(f"✅ Supportfall für {member.mention} wurde erfolgreich zwangsgeschlossen.")
        else:
            await ctx.send(f"⚠️ Konnte den Supportfall nicht beenden (evtl. schon beendet).")

    @lsupportsetup.command(name="lresetstats")
    async def lresetstats(self, ctx: commands.Context):
        """Setzt die Support-Statistiken aller Teamler zurück (z.B. für den Monatswechsel)."""
        await self.config.guild(ctx.guild).stats.set({})
        await ctx.send("✅ Alle Support-Statistiken wurden zurückgesetzt.")

    @lsupportsetup.command(name="lfixnicks")
    async def lfixnicks(self, ctx: commands.Context):
        """Reinigt alle Voice-Channels nach hängengebliebenen Wartenummern."""
        cleaned_count = 0
        for vc in ctx.guild.voice_channels:
            for m in vc.members:
                if self._is_queue_nick(m.nick):
                    await self._reset_member_nick(m, is_fallback=True)
                    cleaned_count += 1

        await ctx.send(f"✅ Globaler Cleanup beendet. {cleaned_count} Nicknames wurden repariert.")

    # ------------------------------------------------------------------
    # Staff-Befehle
    # ------------------------------------------------------------------
    @commands.command(name="lclaimnext")
    @commands.mod_or_permissions(manage_messages=True)
    async def lclaimnext(self, ctx: commands.Context):
        """Zieht den nächsten User aus dem Warteraum in deinen aktuellen Voice-Channel."""
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("❌ Du musst dich in einem Voice-Channel befinden, um das zu tun.")

        waitroom_id = await self.config.guild(ctx.guild).waitroom()
        if waitroom_id and ctx.author.voice.channel.id == waitroom_id:
            return await ctx.send("❌ Du kannst keinen Support aus dem Warteraum heraus übernehmen.")

        sessions = await self.config.guild(ctx.guild).active_sessions()
        waiting_session_id = None
        oldest_time = None
        for msg_id, s_data in sessions.items():
            if s_data.get("status") == "waiting":
                try:
                    start_time = datetime.datetime.fromisoformat(s_data["start_time"])
                except (ValueError, TypeError, KeyError):
                    start_time = datetime.datetime.now(datetime.timezone.utc)
                if oldest_time is None or start_time < oldest_time:
                    oldest_time = start_time
                    waiting_session_id = msg_id

        if not waiting_session_id:
            return await ctx.send("✅ Der Warteraum ist aktuell leer.")

        # FIX: Session vorher atomar reservieren ("claiming"), damit der
        # parallel laufende Voice-Event die Übernahme nicht doppelt abarbeitet.
        async with self.config.guild(ctx.guild).active_sessions() as sessions:
            if waiting_session_id not in sessions or sessions[waiting_session_id].get("status") != "waiting":
                return await ctx.send("❌ Dieser Fall wird bereits übernommen.")
            sessions[waiting_session_id]["status"] = "claiming"
            session = sessions[waiting_session_id]

        target_channel = ctx.author.voice.channel
        moved_any = False
        for u_id in session.get("user_ids", []):
            member = ctx.guild.get_member(u_id)
            if member and member.voice:
                try:
                    await member.move_to(target_channel, reason="Support übernommen (lclaimnext)")
                    orig_nick = session.get("original_nicks", {}).get(str(member.id), None)
                    await self._reset_member_nick(member, orig_nick)
                    moved_any = True
                except Exception:
                    pass

        if not moved_any:
            async with self.config.guild(ctx.guild).active_sessions() as sessions:
                if waiting_session_id in sessions and sessions[waiting_session_id].get("status") == "claiming":
                    sessions[waiting_session_id]["status"] = "waiting"
            return await ctx.send("❌ Ich konnte den User nicht verschieben.")

        await self.start_support(ctx.guild, waiting_session_id, target_channel, ctx.author.id)
        await self._renumber_waiting_nicks(ctx.guild)
        await ctx.send(f"✅ Du hast den nächsten Supportfall übernommen.")

    @commands.command(name="lunmute")
    @commands.mod_or_permissions(manage_messages=True)
    async def lunmute(self, ctx: commands.Context):
        """Entmutet alle Personen in deinem aktuellen Voice-Channel (Notfall-Fix)."""
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await ctx.send("❌ Du musst dich in einem Voice-Channel befinden.")

        channel = ctx.author.voice.channel
        count = 0
        for m in channel.members:
            try:
                if m.voice.mute:
                    await m.edit(mute=False, reason="Notfall-Unmute durch Teamler")
                    count += 1
            except Exception:
                pass

        await ctx.send(f"✅ {count} Personen in {channel.mention} wurden entmutet.")

    @commands.command(name="lsupportstats")
    @commands.mod_or_permissions(manage_messages=True)
    async def lsupportstats(self, ctx: commands.Context):
        """Zeigt Support-Statistiken der Teamler an."""
        stats = await self.config.guild(ctx.guild).stats()
        if not stats:
            return await ctx.send("Noch keine Statistiken verfügbar.")

        embed = discord.Embed(title="📊 Support Statistiken", color=discord.Color.blue())
        sorted_stats = sorted(stats.items(), key=lambda x: x[1].get("count", 0), reverse=True)

        text = ""
        for user_id, data in sorted_stats[:10]:
            user = self.bot.get_user(int(user_id))
            name = user.name if user else "Unbekannt"
            dur = self.format_timedelta(datetime.timedelta(seconds=data.get("duration", 0)))
            text += f"**{name}**: {data.get('count', 0)} Fälle ({dur} gesamt)\n"

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
            if s_data.get("status") in ("waiting", "claiming"):
                try:
                    start_time = datetime.datetime.fromisoformat(s_data["start_time"])
                except (ValueError, TypeError, KeyError):
                    start_time = datetime.datetime.now(datetime.timezone.utc)
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
            end_time = None
            try:
                end_time = datetime.datetime.fromisoformat(cooldowns[str(member.id)])
            except (ValueError, TypeError):
                end_time = None
            if end_time and datetime.datetime.now(datetime.timezone.utc) < end_time:
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
                try:
                    end_time = datetime.datetime.fromisoformat(case["end_time"])
                    end_str = end_time.strftime('%d.%m.%Y')
                except (ValueError, TypeError, KeyError):
                    end_str = "Unbekannt"
                duration_str = self.format_timedelta(datetime.timedelta(seconds=case.get("duration", 0)))
                staff_list = ", ".join([f"<@{s}>" for s in case.get("staff_ids", [])]) or "Unbekannt"
                reason = case.get("reason", "Unbekannt")
                note = case.get("note", "")

                cases_text += f"**Fall {i}** ({end_str}):\n"
                cases_text += f"⏱️ Dauer: {duration_str} | 🎧 Teamler: {staff_list}\n"
                cases_text += f"🚪 Grund: {reason}\n"
                if note:
                    cases_text += f"📝 Notiz: {note}\n"
                cases_text += "\n"

            embed.add_field(name="📜 Verlauf (Letzte 3)", value=cases_text[:1024], inline=False)
        else:
            embed.add_field(name="📜 Verlauf", value="Dieser User hatte bisher noch keine Supportfälle.", inline=False)

        await ctx.send(embed=embed)

# ======================================================================
# UI-Views
# ======================================================================
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

        waitroom_id = await self.cog.config.guild(guild).waitroom()
        if waitroom_id and interaction.user.voice.channel.id == waitroom_id:
            return await interaction.response.send_message("Du kannst keinen Support aus dem Warteraum heraus übernehmen.", ephemeral=True)

        sessions = await self.cog.config.guild(guild).active_sessions()
        if session_id not in sessions:
            return await interaction.response.send_message("Dieser Supportfall existiert nicht mehr.", ephemeral=True)
        status = sessions[session_id].get("status")
        if status in ["active", "paused"]:
            return await interaction.response.send_message("Dieser Fall wurde bereits übernommen. Du kannst einfach in den Channel joinen, um zu helfen!", ephemeral=True)
        if status == "claiming":
            return await interaction.response.send_message("Dieser Fall wird gerade bereits übernommen.", ephemeral=True)

        # FIX: Session wird atomar reserviert ("claiming"). Vorher konnten zwei
        # Teamler gleichzeitig claimen -> der Fall sprang zwischen Channels hin
        # und her, und das parallel laufende Voice-Event hat die Session bei
        # sessions.pop() zerschossen (KeyError) -> tot regellosiger Support.
        async with self.cog.config.guild(guild).active_sessions() as sessions:
            if session_id not in sessions:
                return await interaction.response.send_message("Dieser Supportfall existiert nicht mehr.", ephemeral=True)
            if sessions[session_id].get("status") != "waiting":
                return await interaction.response.send_message("Dieser Fall wird bereits übernommen.", ephemeral=True)
            sessions[session_id]["status"] = "claiming"
            session_data = sessions[session_id]

        await interaction.response.defer(ephemeral=True)

        target_channel = interaction.user.voice.channel
        moved_any = False

        for u_id in session_data.get("user_ids", []):
            member = guild.get_member(u_id)
            if member and member.voice:
                try:
                    await member.move_to(target_channel, reason="Support übernommen")
                    orig_nick = session_data.get("original_nicks", {}).get(str(member.id), None)
                    await self.cog._reset_member_nick(member, orig_nick)
                    moved_any = True
                except Exception:
                    pass

        if not moved_any:
            # FIX: Reservierung zurückrollen, damit der Fall wieder claimbar ist.
            async with self.cog.config.guild(guild).active_sessions() as sessions:
                if session_id in sessions and sessions[session_id].get("status") == "claiming":
                    sessions[session_id]["status"] = "waiting"
            return await interaction.followup.send("Ich konnte keinen Nutzer verschieben (vielleicht haben sie den Voice bereits verlassen?).", ephemeral=True)

        await self.cog.start_support(guild, session_id, target_channel, interaction.user.id)
        await self.cog._renumber_waiting_nicks(guild)
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
        channel_mention = f"<#{session.get('channel_id')}>" if session.get("channel_id") else "N/A"

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
            await self.cog._force_close_embed(guild, session_id, "Von Teamler beendet (war im Hintergrund schon geschlossen)")
            return await interaction.response.send_message("Dieser Supportfall wurde im Hintergrund bereits beendet. Ich habe das Embed für dich rot gemacht.", ephemeral=True)

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
            await self.cog._force_close_embed(guild, self.session_id, "Von Teamler beendet (war im Hintergrund schon geschlossen)", note)
            return await interaction.response.send_message("Support wurde im Hintergrund bereits beendet. Ich habe das Embed für dich rot gemacht.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        success = await self.cog.end_session(guild, self.session_id, "Von Teamler beendet", note)
        if success:
            await interaction.followup.send("Support wurde beendet. Der User wurde aus dem Channel entfernt.", ephemeral=True)
        else:
            await interaction.followup.send("Fehler beim Beenden des Supportfalls.", ephemeral=True)


async def setup(bot: Red):
    await bot.add_cog(SupportSystem(bot))
