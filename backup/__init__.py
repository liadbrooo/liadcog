# -*- coding: utf-8 -*-
"""ServerBackup – Vollständiges Backup-System für Discord-Server (Red v3)

Befehle
-------
[p]backup create [name]     – vollständiges Backup erstellen
[p]backup list              – Backups dieses Servers auflisten
[p]backup info <name>       – Details eines Backups anzeigen
[p]backup restore <name>    – Backup wiederherstellen
[p]backup delete <name>     – Backup löschen

Gesichert werden: Servereinstellungen, alle Rollen, alle Kategorien & Kanäle
(inkl. Rechte-Übernahmen), Emojis (inkl. Bilder), Banliste und die
Rollen-Zuordnung aller Mitglieder. Nachrichten/Threads werden nicht gesichert.

Wiederherstellung ist NICHT destruktiv:
- Vorhandene Rollen/Kanäle (gleiche ID oder gleicher Name) werden nicht
  gelöscht, sondern nur mit den Backup-Einstellungen überschrieben.
- Fehlende Rollen/Kanäle werden neu erstellt.
- Zusätzlich Vorhandenes bleibt unberührt.
"""

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from redbot.core import commands, data_manager
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils.predicates import MessagePredicate

__version__ = "1.0.0"
BACKUP_VERSION = 1

log = logging.getLogger("red.serverbackup")

# ForumChannel existiert erst ab discord.py 2.1 – defensiv laden
_FORUM_CHANNEL = getattr(discord, "ForumChannel", None)


class _Progress:
    """Hält eine Statusnachricht aktuell, ohne Rate-Limits zu reizen."""

    __slots__ = ("_message", "_interval", "_last_edit", "_last_text")

    def __init__(self, message: discord.Message, interval: float = 4.0) -> None:
        self._message = message
        self._interval = interval
        self._last_edit = 0.0
        self._last_text = ""

    async def update(self, text: str, *, force: bool = False) -> None:
        if text == self._last_text:
            return
        if not force and (time.monotonic() - self._last_edit) < self._interval:
            return
        try:
            await self._message.edit(content=text)
            self._last_edit = time.monotonic()
            self._last_text = text
        except discord.HTTPException:
            pass

    async def finish(self, text: str) -> None:
        try:
            await self._message.edit(content=text)
        except discord.HTTPException:
            pass


class ServerBackup(commands.Cog):
    """Vollständiges, nicht-destruktives Backup-System für Discord-Server."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._backups_root: Path = data_manager.cog_data_path(self) / "backups"
        self._backups_root.mkdir(parents=True, exist_ok=True)
        self._running: set = set()  # Guild-IDs mit laufendem Vorgang

    # ================================================================== #
    # Dateiverwaltung                                                     #
    # ================================================================== #

    @staticmethod
    def _safe_filename(name: str) -> str:
        cleaned = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name).strip())
        cleaned = cleaned.strip("._")
        return cleaned[:64] or "backup"

    def _list_backups(self, guild_id: int) -> List[Path]:
        try:
            files = list(self._backups_root.glob(f"{guild_id}__*.json"))
        except OSError:
            return []
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    @staticmethod
    def _name_from_path(path: Path, guild_id: int) -> str:
        prefix = f"{guild_id}__"
        name = path.name
        if name.startswith(prefix):
            name = name[len(prefix):]
        return name[:-5] if name.endswith(".json") else name

    def _find_backup(self, guild_id: int, name: str) -> Optional[Path]:
        wanted = self._safe_filename(name).lower()
        for path in self._list_backups(guild_id):
            if self._name_from_path(path, guild_id).lower() == wanted:
                return path
        # Fallback: exakter Dateiname (nur Dateien dieses Servers!)
        direct = (self._backups_root / name).resolve()
        try:
            direct.relative_to(self._backups_root.resolve())
        except ValueError:
            return None
        if direct.exists() and direct.suffix == ".json" and direct.name.startswith(f"{guild_id}__"):
            return direct
        return None

    # ================================================================== #
    # Serialisierung (Backup erstellen)                                   #
    # ================================================================== #

    @staticmethod
    def _channel_type(channel) -> str:
        if isinstance(channel, discord.CategoryChannel):
            return "category"
        if _FORUM_CHANNEL is not None and isinstance(channel, _FORUM_CHANNEL):
            return "forum"
        if isinstance(channel, discord.StageChannel):
            return "stage_voice"
        if isinstance(channel, discord.VoiceChannel):
            return "voice"
        if isinstance(channel, discord.TextChannel):
            return "text"  # Announcement-Kanäle zählen als Text
        return "text"

    def _type_matches(self, channel, ctype: str) -> bool:
        return self._channel_type(channel) == ctype

    @staticmethod
    def _channel_icon(cdata: dict) -> str:
        return {
            "text": "💬",
            "voice": "🔊",
            "stage_voice": "🎙️",
            "forum": "📋",
            "category": "📁",
        }.get(cdata.get("type", "text"), "💬")

    def _serialize_overwrites(self, overwrites) -> List[dict]:
        entries = []
        for target, overwrite in overwrites.items():
            allow, deny = overwrite.pair()
            entry: Dict[str, Any] = {
                "id": str(target.id),
                "name": getattr(target, "name", str(target)),
                "allow": allow.value,
                "deny": deny.value,
            }
            if isinstance(target, discord.Role):
                entry["type"] = "role"
                if target.is_default():
                    entry["everyone"] = True
            else:
                entry["type"] = "member"
            entries.append(entry)
        return entries

    def _serialize_channel(self, channel) -> dict:
        data: Dict[str, Any] = {
            "id": str(channel.id),
            "name": channel.name,
            "type": self._channel_type(channel),
            "position": channel.position,
            "category": None,
            "overwrites": self._serialize_overwrites(channel.overwrites),
        }
        category_id = getattr(channel, "category_id", None)
        if category_id:
            data["category"] = str(category_id)
        if isinstance(channel, discord.TextChannel):
            data["topic"] = channel.topic
            data["nsfw"] = channel.nsfw
            data["slowmode"] = channel.slowmode_delay
            data["default_auto_archive_duration"] = channel.default_auto_archive_duration
        elif isinstance(channel, discord.VoiceChannel):
            data["bitrate"] = channel.bitrate
            data["user_limit"] = channel.user_limit
            data["rtc_region"] = channel.rtc_region
            data["video_quality_mode"] = (
                channel.video_quality_mode.value if channel.video_quality_mode else None
            )
        elif isinstance(channel, discord.StageChannel):
            data["topic"] = channel.topic
        elif _FORUM_CHANNEL is not None and isinstance(channel, _FORUM_CHANNEL):
            data["topic"] = channel.topic
        return data

    async def _serialize_guild_settings(self, guild: discord.Guild) -> dict:
        settings: Dict[str, Any] = {
            "name": guild.name,
            "verification_level": guild.verification_level.value,
            "default_notifications": guild.default_notifications.value,
            "explicit_content_filter": guild.explicit_content_filter.value,
            "afk_timeout": guild.afk_timeout,
            "afk_channel": str(guild.afk_channel.id) if guild.afk_channel else None,
            "system_channel": str(guild.system_channel.id) if guild.system_channel else None,
            "system_channel_flags": guild.system_channel_flags.value,
            "preferred_locale": str(guild.preferred_locale) if guild.preferred_locale else None,
        }
        for key, asset in (("icon", guild.icon), ("banner", guild.banner), ("splash", guild.splash)):
            if asset is not None:
                try:
                    settings[key] = base64.b64encode(await asset.read()).decode("ascii")
                except discord.DiscordException:
                    pass
        return settings

    async def _create_backup(self, guild: discord.Guild, progress: _Progress) -> dict:
        backup: Dict[str, Any] = {
            "version": BACKUP_VERSION,
            "cog_version": __version__,
            "guild_id": str(guild.id),
            "guild_name": guild.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        await progress.update("📤 Sichere Servereinstellungen …", force=True)
        backup["settings"] = await self._serialize_guild_settings(guild)

        await progress.update("📤 Sichere Rollen …")
        backup["everyone_permissions"] = guild.default_role.permissions.value
        roles = []
        for role in sorted(guild.roles, key=lambda r: r.position):
            if role.is_default() or role.managed:  # @everyone, Bot-/Boost-/Integrationsrollen
                continue
            entry: Dict[str, Any] = {
                "id": str(role.id),
                "name": role.name,
                "color": role.colour.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "permissions": role.permissions.value,
                "position": role.position,
                "unicode_emoji": getattr(role, "unicode_emoji", None),
            }
            display_icon = getattr(role, "display_icon", None)
            if isinstance(display_icon, discord.Asset):
                try:
                    entry["icon"] = base64.b64encode(await display_icon.read()).decode("ascii")
                except discord.DiscordException:
                    pass
            roles.append(entry)
        backup["roles"] = roles

        await progress.update("📤 Sichere Kategorien & Kanäle …")
        categories, channels = [], []
        for channel in guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                categories.append(self._serialize_channel(channel))
            else:
                channels.append(self._serialize_channel(channel))
        backup["categories"] = categories
        backup["channels"] = channels

        await progress.update("📤 Sichere Emojis …")
        emojis = []
        for emoji in guild.emojis:
            entry = {"name": emoji.name, "animated": emoji.animated, "image": None}
            try:
                entry["image"] = base64.b64encode(await emoji.read()).decode("ascii")
            except discord.DiscordException:
                log.warning("Bild von Emoji '%s' konnte nicht geladen werden.", emoji.name)
            emojis.append(entry)
        backup["emojis"] = emojis

        await progress.update("📤 Sichere Bans …")
        bans = []
        try:
            async for ban in guild.bans():
                bans.append({"user": str(ban.user.id), "name": str(ban.user), "reason": ban.reason})
        except discord.Forbidden:
            log.info("Banliste konnte nicht gelesen werden (fehlende Berechtigung).")
        except discord.HTTPException as exc:
            log.warning("Banliste konnte nicht gelesen werden: %s", exc)
        backup["bans"] = bans

        await progress.update("📤 Sichere Mitglieder-Rollen …")
        members: Dict[str, List[str]] = {}
        try:
            if not guild.chunked:
                await guild.chunk()
        except Exception:  # noqa: BLE001
            log.warning("Mitglieder konnten nicht vollständig geladen werden.")
        for member in guild.members:
            role_ids = [str(r.id) for r in member.roles if not r.is_default()]
            if role_ids:
                members[str(member.id)] = role_ids
        backup["members"] = members

        backup["counts"] = {
            "roles": len(roles),
            "categories": len(categories),
            "channels": len(channels),
            "emojis": len(emojis),
            "members": len(members),
            "bans": len(bans),
        }
        return backup

    # ================================================================== #
    # Wiederherstellung – Hilfsfunktionen                                 #
    # ================================================================== #

    @staticmethod
    def _role_kwargs(rdata: dict, reason: str) -> dict:
        kwargs: Dict[str, Any] = {
            "name": rdata["name"],
            "permissions": discord.Permissions(int(rdata.get("permissions", 0))),
            "colour": discord.Colour(int(rdata.get("color", 0))),
            "hoist": bool(rdata.get("hoist", False)),
            "mentionable": bool(rdata.get("mentionable", False)),
            "reason": reason,
        }
        if rdata.get("unicode_emoji"):
            kwargs["unicode_emoji"] = rdata["unicode_emoji"]
        return kwargs

    @staticmethod
    async def _apply_role_icon(role: discord.Role, rdata: dict, reason: str) -> None:
        icon = rdata.get("icon")
        if not icon:
            return
        try:
            await role.edit(icon=base64.b64decode(icon), reason=reason)
        except (discord.HTTPException, TypeError):
            pass  # Icon ist optional

    @staticmethod
    def _match_role(guild: discord.Guild, rdata: dict, used_role_ids: set) -> Optional[discord.Role]:
        # 1) Gleiche ID (Restore auf demselben Server)
        try:
            role = guild.get_role(int(rdata["id"]))
        except (KeyError, ValueError, TypeError):
            role = None
        if role is not None and not role.managed and role.id not in used_role_ids:
            return role
        # 2) Gleicher Name -> NICHT löschen, sondern wiederverwenden!
        for candidate in guild.roles:
            if candidate.managed or candidate.is_default():
                continue
            if candidate.id in used_role_ids:
                continue
            if candidate.name == rdata.get("name"):
                return candidate
        return None

    def _match_channel(self, guild: discord.Guild, cdata: dict, parent, used_channel_ids: set):
        ctype = cdata.get("type", "text")
        try:
            candidate = guild.get_channel(int(cdata["id"]))
        except (KeyError, ValueError, TypeError):
            candidate = None
        if (
            candidate is not None
            and not isinstance(candidate, discord.CategoryChannel)
            and candidate.id not in used_channel_ids
            and self._type_matches(candidate, ctype)
        ):
            return candidate
        # 1) Fallback: gleicher Name + Typ + gleiche Kategorie
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel) or ch.id in used_channel_ids:
                continue
            if ch.name != cdata.get("name") or not self._type_matches(ch, ctype):
                continue
            if parent is not None and ch.category_id != parent.id:
                continue
            if parent is None and ch.category_id is not None:
                continue
            return ch
        # 2) Fallback: gleicher Name + Typ (Kategorie ignorieren)
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel) or ch.id in used_channel_ids:
                continue
            if ch.name == cdata.get("name") and self._type_matches(ch, ctype):
                return ch
        return None

    async def _apply_channel_settings(
        self, guild: discord.Guild, channel, cdata: dict, parent, reason: str
    ) -> bool:
        """Aktualisiert einen bestehenden Kanal mit den Backup-Einstellungen."""
        kwargs: Dict[str, Any] = {}
        is_category = isinstance(channel, discord.CategoryChannel)
        if not is_category:
            if parent is not None and channel.category_id != parent.id:
                kwargs["category"] = parent
            elif cdata.get("category") is None and channel.category_id is not None:
                kwargs["category"] = None
        if cdata.get("name") and channel.name != cdata["name"]:
            kwargs["name"] = cdata["name"]

        if isinstance(channel, discord.TextChannel):
            if "topic" in cdata and channel.topic != cdata["topic"]:
                kwargs["topic"] = cdata["topic"]
            if "nsfw" in cdata and bool(channel.nsfw) != bool(cdata["nsfw"]):
                kwargs["nsfw"] = bool(cdata["nsfw"])
            if cdata.get("slowmode") is not None and channel.slowmode_delay != int(cdata["slowmode"]):
                kwargs["slowmode_delay"] = int(cdata["slowmode"])
            if (
                cdata.get("default_auto_archive_duration") is not None
                and channel.default_auto_archive_duration != int(cdata["default_auto_archive_duration"])
            ):
                kwargs["default_auto_archive_duration"] = int(cdata["default_auto_archive_duration"])
        elif isinstance(channel, discord.VoiceChannel):
            if cdata.get("bitrate"):
                bitrate = min(int(cdata["bitrate"]), guild.bitrate_limit)
                if channel.bitrate != bitrate:
                    kwargs["bitrate"] = bitrate
            if "user_limit" in cdata and channel.user_limit != int(cdata["user_limit"] or 0):
                kwargs["user_limit"] = int(cdata["user_limit"] or 0)
            if cdata.get("rtc_region") and channel.rtc_region != cdata["rtc_region"]:
                kwargs["rtc_region"] = cdata["rtc_region"]
            if cdata.get("video_quality_mode"):
                try:
                    vq = discord.VideoQualityMode(int(cdata["video_quality_mode"]))
                    if channel.video_quality_mode != vq:
                        kwargs["video_quality_mode"] = vq
                except (TypeError, ValueError):
                    pass
        elif not is_category:
            # Stage- und Forum-Kanäle: Topic
            if "topic" in cdata and getattr(channel, "topic", None) != cdata["topic"]:
                kwargs["topic"] = cdata["topic"]

        if not kwargs:
            return True
        try:
            await channel.edit(reason=reason, **kwargs)
            return True
        except (discord.HTTPException, TypeError) as exc:
            log.warning("Kanal '%s' konnte nicht aktualisiert werden: %s", cdata.get("name"), exc)
            return False

    async def _create_channel(self, guild: discord.Guild, cdata: dict, parent, reason: str):
        ctype = cdata.get("type", "text")
        name = cdata.get("name", "kanal")
        try:
            if ctype == "voice":
                kwargs: Dict[str, Any] = {"category": parent, "reason": reason}
                if cdata.get("bitrate"):
                    kwargs["bitrate"] = min(int(cdata["bitrate"]), guild.bitrate_limit)
                if "user_limit" in cdata:
                    kwargs["user_limit"] = int(cdata["user_limit"] or 0)
                if cdata.get("rtc_region"):
                    kwargs["rtc_region"] = cdata["rtc_region"]
                if cdata.get("video_quality_mode"):
                    try:
                        kwargs["video_quality_mode"] = discord.VideoQualityMode(int(cdata["video_quality_mode"]))
                    except (TypeError, ValueError):
                        pass
                return await guild.create_voice_channel(name, **kwargs)
            if ctype == "stage_voice":
                kwargs = {"category": parent, "reason": reason}
                if "topic" in cdata:
                    kwargs["topic"] = cdata["topic"]
                return await guild.create_stage_channel(name, **kwargs)
            if ctype == "forum":
                if not hasattr(guild, "create_forum"):
                    log.warning("Forum-Kanäle werden von dieser discord.py-Version nicht unterstützt.")
                    return None
                kwargs = {"category": parent, "reason": reason}
                if "topic" in cdata:
                    kwargs["topic"] = cdata["topic"]
                return await guild.create_forum(name, **kwargs)
            # Standard: Textkanal
            kwargs = {"category": parent, "reason": reason}
            if "topic" in cdata:
                kwargs["topic"] = cdata["topic"]
            if "nsfw" in cdata:
                kwargs["nsfw"] = bool(cdata["nsfw"])
            if cdata.get("slowmode") is not None:
                kwargs["slowmode_delay"] = int(cdata["slowmode"])
            if cdata.get("default_auto_archive_duration") is not None:
                kwargs["default_auto_archive_duration"] = int(cdata["default_auto_archive_duration"])
            return await guild.create_text_channel(name, **kwargs)
        except (discord.HTTPException, TypeError) as exc:
            log.warning("Kanal '%s' (%s) konnte nicht erstellt werden: %s", name, ctype, exc)
            return None

    # ================================================================== #
    # Wiederherstellung                                                   #
    # ================================================================== #

    async def _restore_backup(
        self,
        guild: discord.Guild,
        backup: dict,
        progress: _Progress,
        *,
        skip_emojis: bool = False,
        skip_members: bool = False,
        skip_bans: bool = False,
        skip_settings: bool = False,
        sync_positions: bool = True,
    ) -> Dict[str, int]:
        reason = (
            f"ServerBackup: Wiederherstellung von '{backup.get('guild_name', '?')}' "
            f"({str(backup.get('created_at', ''))[:10]})"
        )[:480]
        role_map: Dict[str, discord.Role] = {}
        channel_map: Dict[str, Any] = {}
        stats = {
            "roles_created": 0,
            "roles_updated": 0,
            "channels_created": 0,
            "channels_updated": 0,
            "emojis_created": 0,
            "members_updated": 0,
            "bans_restored": 0,
            "errors": 0,
        }

        # -------------------------------------------------------------- #
        # 1/8 – Rollen                                                    #
        # -------------------------------------------------------------- #
        role_list = sorted(backup.get("roles") or [], key=lambda r: r.get("position", 0))
        used_role_ids = {guild.default_role.id}
        to_create: List[dict] = []

        everyone_perms = backup.get("everyone_permissions")
        if everyone_perms is not None:
            try:
                await guild.default_role.edit(
                    permissions=discord.Permissions(int(everyone_perms)), reason=reason
                )
            except discord.HTTPException as exc:
                log.warning("@everyone-Rechte konnten nicht gesetzt werden: %s", exc)
                stats["errors"] += 1

        # Bestehende Rollen nur AKTUALISIEREN (niemals löschen)
        for idx, rdata in enumerate(role_list, start=1):
            await progress.update(f"🔄 1/8 Rollen prüfen … ({idx}/{len(role_list)})")
            role = self._match_role(guild, rdata, used_role_ids)
            if role is None:
                to_create.append(rdata)
                continue
            try:
                await role.edit(**self._role_kwargs(rdata, reason))
                await self._apply_role_icon(role, rdata, reason)
                stats["roles_updated"] += 1
            except (discord.HTTPException, TypeError) as exc:
                log.warning("Rolle '%s' konnte nicht aktualisiert werden: %s", rdata.get("name"), exc)
                stats["errors"] += 1
            used_role_ids.add(role.id)
            role_map[str(rdata["id"])] = role

        # Fehlende Rollen erstellen (von oben nach unten => korrekte Reihenfolge)
        for rdata in reversed(to_create):
            await progress.update(f"🔄 1/8 Rollen erstellen … ({len(role_map)}/{len(role_list)})")
            try:
                new_role = await guild.create_role(**self._role_kwargs(rdata, reason))
                await self._apply_role_icon(new_role, rdata, reason)
                used_role_ids.add(new_role.id)
                role_map[str(rdata["id"])] = new_role
                stats["roles_created"] += 1
            except (discord.HTTPException, TypeError) as exc:
                log.warning("Rolle '%s' konnte nicht erstellt werden: %s", rdata.get("name"), exc)
                stats["errors"] += 1
            await asyncio.sleep(0.5)  # Rollen-Erstellung ist stark rate-limited

        # Rollen-Reihenfolge an Backup angleichen
        if sync_positions and role_map:
            await progress.update("🔄 1/8 Rollen-Reihenfolge …", force=True)
            ordered = [role_map[str(r["id"])] for r in role_list if str(r["id"]) in role_map]
            for target, role in enumerate(ordered, start=1):
                if role.position == target:
                    continue
                if role.position >= guild.me.top_role.position:
                    continue  # über der Bot-Rolle nicht verschiebbar
                try:
                    await role.edit(position=target, reason=reason)
                    await asyncio.sleep(0.25)
                except discord.HTTPException as exc:
                    log.warning("Position von Rolle '%s' fehlgeschlagen: %s", role.name, exc)

        # -------------------------------------------------------------- #
        # 2/8 – Kategorien                                                #
        # -------------------------------------------------------------- #
        used_channel_ids: set = set()
        cat_list = sorted(backup.get("categories") or [], key=lambda c: c.get("position", 0))
        for idx, cdata in enumerate(cat_list, start=1):
            await progress.update(f"🔄 2/8 Kategorien … ({idx}/{len(cat_list)})")
            cat = None
            candidate = guild.get_channel(int(cdata["id"])) if str(cdata.get("id", "")).isdigit() else None
            if isinstance(candidate, discord.CategoryChannel) and candidate.id not in used_channel_ids:
                cat = candidate
            if cat is None:
                for c in guild.categories:
                    if c.id not in used_channel_ids and c.name == cdata.get("name"):
                        cat = c
                        break
            if cat is not None:
                try:
                    if cat.name != cdata.get("name"):
                        await cat.edit(name=cdata["name"], reason=reason)
                    stats["channels_updated"] += 1
                except discord.HTTPException as exc:
                    log.warning("Kategorie '%s' konnte nicht aktualisiert werden: %s", cdata.get("name"), exc)
                    stats["errors"] += 1
            else:
                try:
                    cat = await guild.create_category(cdata["name"], reason=reason)
                    stats["channels_created"] += 1
                    await asyncio.sleep(0.3)
                except discord.HTTPException as exc:
                    log.warning("Kategorie '%s' konnte nicht erstellt werden: %s", cdata.get("name"), exc)
                    stats["errors"] += 1
            if cat is not None:
                used_channel_ids.add(cat.id)
                channel_map[str(cdata["id"])] = cat

        # -------------------------------------------------------------- #
        # 3/8 – Kanäle                                                    #
        # -------------------------------------------------------------- #
        ch_list = sorted(backup.get("channels") or [], key=lambda c: c.get("position", 0))
        for idx, cdata in enumerate(ch_list, start=1):
            await progress.update(f"🔄 3/8 Kanäle … ({idx}/{len(ch_list)})")
            parent = channel_map.get(cdata.get("category")) if cdata.get("category") else None
            ch = self._match_channel(guild, cdata, parent, used_channel_ids)
            if ch is not None:
                # Vorhanden -> nur Einstellungen überschreiben, NICHT löschen
                if not await self._apply_channel_settings(guild, ch, cdata, parent, reason):
                    stats["errors"] += 1
                else:
                    stats["channels_updated"] += 1
                used_channel_ids.add(ch.id)
                channel_map[str(cdata["id"])] = ch
            else:
                ch = await self._create_channel(guild, cdata, parent, reason)
                if ch is not None:
                    used_channel_ids.add(ch.id)
                    channel_map[str(cdata["id"])] = ch
                    stats["channels_created"] += 1
                else:
                    stats["errors"] += 1
                await asyncio.sleep(0.3)

        # -------------------------------------------------------------- #
        # 4/8 – Berechtigungen (Overwrites) – nur obendrauf, nichts weg  #
        # -------------------------------------------------------------- #
        all_channel_data = list(backup.get("categories") or []) + list(backup.get("channels") or [])
        ow_total = sum(len(c.get("overwrites") or []) for c in all_channel_data)
        ow_done = 0
        for cdata in all_channel_data:
            channel = channel_map.get(str(cdata.get("id")))
            if channel is None:
                continue
            for ow in cdata.get("overwrites") or []:
                ow_done += 1
                if ow_done % 5 == 0 or ow_done == ow_total:
                    await progress.update(f"🔄 4/8 Berechtigungen … ({ow_done}/{ow_total})")
                allow = int(ow.get("allow", 0))
                deny = int(ow.get("deny", 0))
                if not allow and not deny:
                    continue
                try:
                    if ow.get("everyone"):
                        target = guild.default_role
                    elif ow.get("type") == "role":
                        target = role_map.get(ow.get("id")) or guild.get_role(int(ow["id"]))
                    else:
                        target = guild.get_member(int(ow["id"]))
                except (KeyError, ValueError, TypeError):
                    continue
                if target is None:
                    continue
                try:
                    overwrite = discord.PermissionOverwrite.from_pair(
                        discord.Permissions(allow), discord.Permissions(deny)
                    )
                    await channel.set_permissions(target, overwrite=overwrite, reason=reason)
                except (discord.HTTPException, ValueError) as exc:
                    log.warning("Overwrite für '%s' fehlgeschlagen: %s", cdata.get("name"), exc)
                    stats["errors"] += 1

        # -------------------------------------------------------------- #
        # 5/8 – Kanal-Reihenfolge                                         #
        # -------------------------------------------------------------- #
        if sync_positions:
            groups: Dict[str, list] = {}
            for cdata in cat_list + ch_list:
                ch = channel_map.get(str(cdata.get("id")))
                if ch is None:
                    continue
                groups.setdefault(cdata.get("type", "text"), []).append((cdata, ch))
            pos_total = sum(len(v) for v in groups.values())
            pos_done = 0
            for items in groups.values():
                items.sort(key=lambda item: item[0].get("position", 0))
                for cdata, ch in items:
                    pos_done += 1
                    if pos_done % 5 == 0:
                        await progress.update(f"🔄 5/8 Kanal-Reihenfolge … ({pos_done}/{pos_total})")
                    target = cdata.get("position")
                    if target is None or ch.position == target:
                        continue
                    try:
                        await ch.edit(position=target, reason=reason)
                        await asyncio.sleep(0.25)
                    except discord.HTTPException as exc:
                        log.warning("Position von '%s' fehlgeschlagen: %s", cdata.get("name"), exc)

        # -------------------------------------------------------------- #
        # 6/8 – Servereinstellungen                                       #
        # -------------------------------------------------------------- #
        if not skip_settings:
            await progress.update("🔄 6/8 Servereinstellungen …", force=True)
            s = backup.get("settings") or {}
            if not guild.me.guild_permissions.manage_guild:
                log.info("Servereinstellungen übersprungen ('Server verwalten' fehlt).")
            else:
                if s.get("name"):
                    try:
                        await guild.edit(name=str(s["name"]), reason=reason)
                    except discord.HTTPException as exc:
                        log.warning("Servername konnte nicht gesetzt werden: %s", exc)
                        stats["errors"] += 1
                for image_key in ("icon", "banner", "splash"):
                    if s.get(image_key):
                        try:
                            await guild.edit(
                                **{image_key: base64.b64decode(s[image_key])}, reason=reason
                            )
                        except (discord.HTTPException, TypeError) as exc:
                            log.warning("%s konnte nicht gesetzt werden: %s", image_key, exc)
                kwargs: Dict[str, Any] = {}
                for key, enum_cls in (
                    ("verification_level", discord.VerificationLevel),
                    ("default_notifications", discord.NotificationLevel),
                    ("explicit_content_filter", discord.ContentFilter),
                    ("system_channel_flags", discord.SystemChannelFlags),
                ):
                    if key in s:
                        try:
                            kwargs[key] = enum_cls(int(s[key]))
                        except (ValueError, TypeError):
                            pass
                if "afk_timeout" in s:
                    try:
                        kwargs["afk_timeout"] = int(s["afk_timeout"])
                    except (TypeError, ValueError):
                        pass
                if guild.me.guild_permissions.manage_channels:
                    afk = channel_map.get(s.get("afk_channel"))
                    if afk is not None:
                        kwargs["afk_channel"] = afk
                    system = channel_map.get(s.get("system_channel"))
                    if system is not None:
                        kwargs["system_channel"] = system
                if kwargs:
                    try:
                        await guild.edit(**kwargs, reason=reason)
                    except (discord.HTTPException, TypeError) as exc:
                        log.warning("Servereinstellungen teilweise fehlgeschlagen: %s", exc)
                        stats["errors"] += 1

        # -------------------------------------------------------------- #
        # 7/8 – Emojis (vorhandene bleiben, fehlende werden erstellt)    #
        # -------------------------------------------------------------- #
        emojis = [e for e in (backup.get("emojis") or []) if e.get("image")]
        if not skip_emojis and emojis:
            if not guild.me.guild_permissions.manage_emojis:
                log.info("Emojis übersprungen ('Emojis verwalten' fehlt).")
            else:
                existing_names = {e.name for e in guild.emojis}
                for idx, edata in enumerate(emojis, start=1):
                    await progress.update(f"🔄 7/8 Emojis … ({idx}/{len(emojis)})")
                    if edata.get("name") in existing_names:
                        continue
                    if len(guild.emojis) >= guild.emoji_limit:
                        log.warning("Emoji-Slots voll – weitere Emojis übersprungen.")
                        break
                    try:
                        await guild.create_custom_emoji(
                            name=str(edata["name"]),
                            image=base64.b64decode(edata["image"]),
                            reason=reason,
                        )
                        stats["emojis_created"] += 1
                        await asyncio.sleep(0.5)
                    except discord.HTTPException as exc:
                        log.warning("Emoji '%s' fehlgeschlagen: %s", edata.get("name"), exc)
                        stats["errors"] += 1

        # -------------------------------------------------------------- #
        # 8/8 – Mitglieder-Rollen & Bans                                  #
        # -------------------------------------------------------------- #
        members_data = backup.get("members") or {}
        if not skip_members and members_data:
            me_top_pos = guild.me.top_role.position
            entries = list(members_data.items())
            for idx, (user_id, role_ids) in enumerate(entries, start=1):
                if idx % 25 == 0 or idx == len(entries):
                    await progress.update(f"🔄 8/8 Mitglieder-Rollen … ({idx}/{len(entries)})")
                try:
                    member = guild.get_member(int(user_id))
                except (TypeError, ValueError):
                    continue
                if member is None:
                    continue  # Mitglied nicht (mehr) auf dem Server
                missing = []
                for rid in role_ids:
                    role = role_map.get(rid) or guild.get_role(int(rid))
                    if role is None or role.is_default() or role.managed:
                        continue
                    if role in member.roles:
                        continue
                    if role.position >= me_top_pos:
                        continue  # Bot darf diese Rolle nicht vergeben
                    missing.append(role)
                if missing:
                    try:
                        await member.add_roles(*missing, reason=reason)
                        stats["members_updated"] += 1
                        await asyncio.sleep(0.25)
                    except discord.HTTPException as exc:
                        log.warning("Rollen für %s fehlgeschlagen: %s", member, exc)
                        stats["errors"] += 1

        bans = backup.get("bans") or []
        if not skip_bans and bans:
            if not guild.me.guild_permissions.ban_members:
                log.info("Bans übersprungen ('Mitglieder verbannen' fehlt).")
            else:
                current_bans = set()
                try:
                    async for entry in guild.bans():
                        current_bans.add(entry.user.id)
                except discord.Forbidden:
                    pass
                pending = [b for b in bans if str(b.get("user", "")).isdigit() and int(b["user"]) not in current_bans]
                for idx, bdata in enumerate(pending, start=1):
                    if idx % 10 == 0 or idx == len(pending):
                        await progress.update(f"🔄 8/8 Bans … ({idx}/{len(pending)})")
                    ban_reason = f"ServerBackup: {bdata.get('reason') or 'aus Backup'}"[:500]
                    try:
                        await guild.ban(discord.Object(id=int(bdata["user"])), reason=ban_reason)
                        stats["bans_restored"] += 1
                        await asyncio.sleep(0.5)
                    except (discord.HTTPException, KeyError, ValueError) as exc:
                        log.warning("Ban für %s fehlgeschlagen: %s", bdata.get("user"), exc)
                        stats["errors"] += 1

        return stats

    # ================================================================== #
    # Befehle                                                             #
    # ================================================================== #

    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    @commands.group(name="backup", aliases=["serverbackup", "sbackup"])
    async def backup_cmd(self, ctx: commands.Context):
        """Vollständiges Backup-System für Discord-Server."""
        await ctx.send_help(ctx.command)

    @backup_cmd.command(name="create", aliases=["erstellen", "neu", "save"])
    async def backup_create(self, ctx: commands.Context, *, name: Optional[str] = None):
        """Erstellt ein vollständiges Backup dieses Servers.

        Gesichert werden: Servereinstellungen, alle Rollen, alle Kategorien
        und Kanäle inkl. Rechten, Emojis, Bans und die Rollen-Zuordnung aller
        Mitglieder. Nachrichten werden nicht gesichert.
        """
        if ctx.guild.id in self._running:
            return await ctx.send("⚠️ Auf diesem Server läuft bereits ein Backup-Vorgang.")
        self._running.add(ctx.guild.id)
        try:
            status = await ctx.send("📤 Backup wird erstellt – bei großen Servern kann das etwas dauern …")
            progress = _Progress(status)
            data = await self._create_backup(ctx.guild, progress)

            base = name or datetime.now().strftime("backup_%Y-%m-%d_%H-%M")
            safe = self._safe_filename(base)
            path = self._backups_root / f"{ctx.guild.id}__{safe}.json"
            suffix = 1
            while path.exists():
                suffix += 1
                path = self._backups_root / f"{ctx.guild.id}__{safe}_{suffix}.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            backup_name = self._name_from_path(path, ctx.guild.id)
            size_kb = path.stat().st_size / 1024
            counts = data.get("counts", {})
            await progress.finish(f"✅ Backup `{backup_name}` erstellt ({size_kb:.1f} KB).")
            await ctx.send(
                f"✅ **Backup `{backup_name}` gespeichert** ({size_kb:.1f} KB)\n"
                f"📁 {counts.get('roles', 0)} Rollen · {counts.get('categories', 0)} Kategorien · "
                f"{counts.get('channels', 0)} Kanäle · {counts.get('emojis', 0)} Emojis · "
                f"{counts.get('members', 0)} Mitglieder-Rollen · {counts.get('bans', 0)} Bans\n"
                f"▶️ Wiederherstellen: `{ctx.clean_prefix}backup restore {backup_name}`"
            )
        except Exception:  # noqa: BLE001
            log.exception("Fehler beim Erstellen des Backups")
            await ctx.send("❌ Das Backup konnte nicht erstellt werden – Details stehen im Log.")
        finally:
            self._running.discard(ctx.guild.id)

    @backup_cmd.command(name="list", aliases=["liste", "ls"])
    async def backup_list(self, ctx: commands.Context):
        """Zeigt alle gespeicherten Backups dieses Servers."""
        files = self._list_backups(ctx.guild.id)
        if not files:
            return await ctx.send(
                f"📭 Keine Backups vorhanden. Erstelle eines mit `{ctx.clean_prefix}backup create`."
            )
        lines = []
        for path in files:
            name = self._name_from_path(path, ctx.guild.id)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                created = str(data.get("created_at", "?"))[:19].replace("T", " ")
                counts = data.get("counts", {})
                lines.append(
                    f"**{name}** – {created} UTC – {path.stat().st_size / 1024:.0f} KB\n"
                    f"↳ {counts.get('roles', 0)} Rollen · {counts.get('categories', 0)} Kategorien · "
                    f"{counts.get('channels', 0)} Kanäle · {counts.get('emojis', 0)} Emojis · "
                    f"{counts.get('members', 0)} Mitglieder · {counts.get('bans', 0)} Bans"
                )
            except (OSError, json.JSONDecodeError):
                lines.append(f"**{name}** – ⚠️ Datei beschädigt")
        for page in pagify("\n".join(lines), page_length=1900):
            await ctx.send(page)

    @backup_cmd.command(name="info", aliases=["show", "details"])
    async def backup_info(self, ctx: commands.Context, *, name: str):
        """Zeigt Details zu einem gespeicherten Backup."""
        path = self._find_backup(ctx.guild.id, name)
        if path is None:
            return await ctx.send(f"❌ Backup `{name}` nicht gefunden.")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return await ctx.send("❌ Backup-Datei ist beschädigt.")

        counts = data.get("counts", {})
        created = str(data.get("created_at", "?"))[:19].replace("T", " ")
        lines = [
            f"📦 **Backup:** `{self._name_from_path(path, ctx.guild.id)}`",
            f"📅 **Erstellt am:** {created} UTC",
            f"🏠 **Server:** {data.get('guild_name', '?')} (`{data.get('guild_id', '?')}`)",
            f"💾 **Größe:** {path.stat().st_size / 1024:.1f} KB",
            f"🔢 **Inhalt:** {counts.get('roles', 0)} Rollen · {counts.get('categories', 0)} Kategorien · "
            f"{counts.get('channels', 0)} Kanäle · {counts.get('emojis', 0)} Emojis · "
            f"{counts.get('members', 0)} Mitglieder · {counts.get('bans', 0)} Bans",
            "",
            "**Rollen:** " + (", ".join(f"`{r['name']}`" for r in data.get("roles", [])) or "–"),
            "",
            "**Kanalstruktur:**",
        ]
        categories = {str(c["id"]): c for c in data.get("categories", [])}
        assigned = set()
        for cid, cdata in categories.items():
            lines.append(f"📁 **{cdata['name']}**")
            for ch in data.get("channels", []):
                if ch.get("category") == cid:
                    lines.append(f"   ↳ {self._channel_icon(ch)} {ch['name']}")
                    assigned.add(str(ch["id"]))
        orphans = [ch for ch in data.get("channels", []) if str(ch["id"]) not in assigned]
        if orphans:
            lines.append("📁 **(ohne Kategorie)**")
            for ch in orphans:
                lines.append(f"   ↳ {self._channel_icon(ch)} {ch['name']}")
        for page in pagify("\n".join(lines), page_length=1900):
            await ctx.send(page)

    @backup_cmd.command(name="restore", aliases=["wiederherstellen", "load", "laden"])
    async def backup_restore(self, ctx: commands.Context, name: str, *options):
        """Stellt ein Backup wieder her – ohne etwas zu löschen.

        Bereits vorhandene Rollen/Kanäle werden beibehalten und nur mit den
        Backup-Einstellungen überschrieben. Fehlende werden neu erstellt.

        Optionale Schalter:
            --skip-emojis     Emojis nicht wiederherstellen
            --skip-members    Mitglieder-Rollen nicht wiederherstellen
            --skip-bans       Bans nicht wiederherstellen
            --skip-settings   Servereinstellungen nicht überschreiben
            --skip-positions  Reihenfolge von Rollen/Kanälen nicht anpassen
        """
        if ctx.guild.id in self._running:
            return await ctx.send("⚠️ Auf diesem Server läuft bereits ein Backup-Vorgang.")
        path = self._find_backup(ctx.guild.id, name)
        if path is None:
            return await ctx.send(
                f"❌ Backup `{name}` nicht gefunden. Alle Backups: `{ctx.clean_prefix}backup list`"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return await ctx.send(f"❌ Backup-Datei konnte nicht gelesen werden: `{exc}`")

        opts = {str(o).lower() for o in options}
        known = {"--skip-emojis", "--skip-members", "--skip-bans", "--skip-settings", "--skip-positions"}
        unknown = opts - known
        if unknown:
            await ctx.send(f"ℹ️ Unbekannte Option(en) ignoriert: {', '.join(sorted(unknown))}")

        guild = ctx.guild
        perms = guild.me.guild_permissions
        if not perms.manage_roles or not perms.manage_channels:
            missing = []
            if not perms.manage_roles:
                missing.append("**Rollen verwalten**")
            if not perms.manage_channels:
                missing.append("**Kanäle verwalten**")
            return await ctx.send("❌ Mir fehlen folgende Berechtigungen: " + " · ".join(missing))

        counts = data.get("counts", {})
        created = str(data.get("created_at", "?"))[:19].replace("T", " ")
        warn_lines = []
        if not perms.manage_guild:
            warn_lines.append("Servereinstellungen werden übersprungen ('Server verwalten' fehlt).")
        if not perms.manage_emojis:
            warn_lines.append("Emojis werden übersprungen ('Emojis verwalten' fehlt).")
        if not perms.ban_members:
            warn_lines.append("Bans werden übersprungen ('Mitglieder verbannen' fehlt).")

        question = (
            f"**Backup:** `{self._name_from_path(path, guild.id)}`\n"
            f"**Erstellt:** {created} UTC\n"
            f"**Inhalt:** {counts.get('roles', 0)} Rollen · {counts.get('categories', 0)} Kategorien · "
            f"{counts.get('channels', 0)} Kanäle · {counts.get('emojis', 0)} Emojis · "
            f"{counts.get('members', 0)} Mitglieder-Rollen · {counts.get('bans', 0)} Bans\n\n"
            "**So funktioniert die Wiederherstellung:**\n"
            "• Bereits vorhandene Rollen/Kanäle werden **nicht gelöscht** – sie erhalten nur die "
            "Einstellungen aus dem Backup.\n"
            "• Fehlende Rollen/Kanäle werden neu erstellt.\n"
            "• Zusätzlich vorhandene Rollen/Kanäle bleiben unberührt.\n"
            "• Servereinstellungen, Emojis, Bans und Mitglieder-Rollen werden aus dem Backup übernommen.\n\n"
            "ℹ️ Je nach Servergröße kann das **mehrere Minuten** dauern (Discord-Rate-Limits).\n\n"
            "**Wirklich starten?** (Antworte mit `yes` oder `no`)"
        )
        if warn_lines:
            question += "\n\n⚠️ " + "\n⚠️ ".join(warn_lines)
        await ctx.send(question)

        pred = MessagePredicate.yes_or_no(ctx)
        try:
            await self.bot.wait_for("message", check=pred, timeout=120)
        except asyncio.TimeoutError:
            return await ctx.send("⏹ Keine Antwort – Wiederherstellung abgebrochen.")
        if not pred.result:
            return await ctx.send("❌ Wiederherstellung abgebrochen.")
        if guild.id in self._running:
            return await ctx.send("⚠️ Gerade läuft bereits ein anderer Backup-Vorgang.")

        status = await ctx.send("🔄 **Wiederherstellung gestartet …**")
        progress = _Progress(status)
        self._running.add(guild.id)
        try:
            stats = await self._restore_backup(
                guild,
                data,
                progress,
                skip_emojis="--skip-emojis" in opts,
                skip_members="--skip-members" in opts,
                skip_bans="--skip-bans" in opts,
                skip_settings="--skip-settings" in opts,
                sync_positions="--skip-positions" not in opts,
            )
            await progress.finish("✅ **Wiederherstellung abgeschlossen.**")
            summary = [
                "✅ **Wiederherstellung abgeschlossen**",
                f"🎭 Rollen: **{stats['roles_created']}** neu erstellt, **{stats['roles_updated']}** aktualisiert",
                f"📺 Kategorien/Kanäle: **{stats['channels_created']}** neu erstellt, "
                f"**{stats['channels_updated']}** aktualisiert",
                f"😀 Emojis: **{stats['emojis_created']}** neu erstellt",
                f"👥 Mitglieder-Rollen: **{stats['members_updated']}** aktualisiert",
                f"🔨 Bans: **{stats['bans_restored']}** wiederhergestellt",
            ]
            if stats["errors"]:
                summary.append(
                    f"⚠️ {stats['errors']} Einzelprobleme (Details im Log – z. B. fehlende Rechte "
                    "oder Discord-Limits). Der Vorgang kann bedenkenlos wiederholt werden."
                )
            await ctx.send("\n".join(summary))
        except Exception:  # noqa: BLE001
            log.exception("Fehler bei der Backup-Wiederherstellung")
            await progress.finish("⚠️ **Fehler während der Wiederherstellung** – Details im Log.")
            await ctx.send("❌ Die Wiederherstellung wurde unterbrochen. Bitte Log prüfen.")
        finally:
            self._running.discard(guild.id)

    @backup_cmd.command(name="delete", aliases=["del", "remove", "löschen", "entfernen"])
    async def backup_delete(self, ctx: commands.Context, *, name: str):
        """Löscht ein gespeichertes Backup."""
        path = self._find_backup(ctx.guild.id, name)
        if path is None:
            return await ctx.send(f"❌ Backup `{name}` nicht gefunden.")
        backup_name = self._name_from_path(path, ctx.guild.id)
        await ctx.send(f"⚠️ Backup `{backup_name}` wirklich löschen? (`yes`/`no`)")
        pred = MessagePredicate.yes_or_no(ctx)
        try:
            await self.bot.wait_for("message", check=pred, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send("⏹ Keine Antwort – Löschung abgebrochen.")
        if not pred.result:
            return await ctx.send("❌ Löschung abgebrochen.")
        try:
            path.unlink()
        except OSError as exc:
            return await ctx.send(f"❌ Datei konnte nicht gelöscht werden: `{exc}`")
        await ctx.send(f"🗑️ Backup `{backup_name}` gelöscht.")

    # ================================================================== #
    # Red-Datenschutz-Schnittstelle (End User Data)                       #
    # ================================================================== #

    async def red_get_data_for_user(self, *, user_id: int):
        uid = str(user_id)
        results = []
        for path in self._backups_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            found = []
            if uid in (data.get("members") or {}):
                found.append("Rollen-Zuordnung")
            if any(b.get("user") == uid for b in (data.get("bans") or [])):
                found.append("Ban-Eintrag")
            for section in ("categories", "channels"):
                if any(
                    o.get("id") == uid and o.get("type") == "member"
                    for c in (data.get(section) or [])
                    for o in (c.get("overwrites") or [])
                ):
                    found.append("Kanal-Berechtigungen")
                    break
            if found:
                results.append(f"{path.name}: {', '.join(found)}")
        return {"stored_data": "\n".join(results) or "Keine Daten gespeichert."}

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        uid = str(user_id)
        for path in self._backups_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            changed = False
            members = data.get("members") or {}
            if uid in members:
                del members[uid]
                changed = True
            bans = data.get("bans") or []
            new_bans = [b for b in bans if b.get("user") != uid]
            if len(new_bans) != len(bans):
                data["bans"] = new_bans
                changed = True
            for section in ("categories", "channels"):
                for channel in data.get(section) or []:
                    overwrites = channel.get("overwrites") or []
                    new_ow = [o for o in overwrites if not (o.get("id") == uid and o.get("type") == "member")]
                    if len(new_ow) != len(overwrites):
                        channel["overwrites"] = new_ow
                        changed = True
            if changed:
                try:
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    log.warning("Konnte %s nicht aktualisieren.", path.name)


async def setup(bot):
    await bot.add_cog(ServerBackup(bot))
