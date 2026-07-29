import discord
from dashboard import dashboard
from dashboard.utilities import has_permissions
from flask import render_template_string, request, flash, redirect

@dashboard.route("/supportsystem", methods=["GET", "POST"])
@has_permissions(manage_guild=True)
async def supportsystem_settings(user: discord.User, guild: discord.Guild):
    """Die Custom-Einstellungsseite für das Support-System im Red Dashboard."""
    
    bot = dashboard.bot
    cog = bot.get_cog("SupportSystem")
    if not cog:
        return render_template_string("<h1>SupportSystem Cog ist nicht geladen.</h1>")

    config = cog.config.guild(guild)

    if request.method == "POST":
        waitroom_id = request.form.get("waitroom", type=int)
        staff_channel_id = request.form.get("staff_channel", type=int)
        staff_role_id = request.form.get("staff_role", type=int)
        log_channel_id = request.form.get("log_channel", type=int)
        cooldown = request.form.get("cooldown", type=int, default=300)

        if waitroom_id: await config.waitroom.set(waitroom_id)
        if staff_channel_id: await config.staff_channel.set(staff_channel_id)
        if staff_role_id: await config.staff_role.set(staff_role_id)
        if log_channel_id: await config.log_channel.set(log_channel_id)
        await config.cooldown.set(cooldown)

        flash("✅ Support-System Einstellungen erfolgreich gespeichert!", "success")
        return redirect(request.url)

    waitroom_id = await config.waitroom()
    staff_channel_id = await config.staff_channel()
    staff_role_id = await config.staff_role()
    log_channel_id = await config.log_channel()
    cooldown = await config.cooldown()

    html = """
    {% extends "base.html" %}
    {% block content %}
    <div class="container mt-4">
        <h1 class="mb-4">🎧 Support System Einstellungen</h1>
        <p>Hier kannst du das Support-System bequem über das Dashboard einrichten.</p>
        
        <form method="POST">
            <div class="mb-3">
                <label class="form-label">🔊 Warteraum (Voice Channel)</label>
                <select name="waitroom" class="form-select">
                    <option value="">-- Nicht gesetzt --</option>
                    {% for c in voice_channels %}
                    <option value="{{ c.id }}" {% if c.id == waitroom %}selected{% endif %}>{{ c.name }}</option>
                    {% endfor %}
                </select>
            </div>

            <div class="mb-3">
                <label class="form-label">📋 Staff-Channel (Text Channel für Pings)</label>
                <select name="staff_channel" class="form-select">
                    <option value="">-- Nicht gesetzt --</option>
                    {% for c in text_channels %}
                    <option value="{{ c.id }}" {% if c.id == staff_channel %}selected{% endif %}>{{ c.name }}</option>
                    {% endfor %}
                </select>
            </div>

            <div class="mb-3">
                <label class="form-label">📡 Log-Channel (Text Channel für Archiv)</label>
                <select name="log_channel" class="form-select">
                    <option value="">-- Nicht gesetzt --</option>
                    {% for c in text_channels %}
                    <option value="{{ c.id }}" {% if c.id == log_channel %}selected{% endif %}>{{ c.name }}</option>
                    {% endfor %}
                </select>
            </div>

            <div class="mb-3">
                <label class="form-label">👑 Haupt-Support-Rolle (Wird gepingt)</label>
                <select name="staff_role" class="form-select">
                    <option value="">-- Nicht gesetzt --</option>
                    {% for r in roles %}
                    <option value="{{ r.id }}" {% if r.id == staff_role %}selected{% endif %}>{{ r.name }}</option>
                    {% endfor %}
                </select>
            </div>

            <div class="mb-3">
                <label class="form-label">⏱️ Cooldown (in Sekunden)</label>
                <input type="number" class="form-control" name="cooldown" value="{{ cooldown }}" min="0">
            </div>

            <button type="submit" class="btn btn-primary">Speichern</button>
        </form>
    </div>
    {% endblock %}
    """

    return render_template_string(
        html, 
        waitroom=waitroom_id, 
        staff_channel=staff_channel_id, 
        staff_role=staff_role_id, 
        log_channel=log_channel_id, 
        cooldown=cooldown,
        voice_channels=guild.voice_channels,
        text_channels=guild.text_channels,
        roles=guild.roles
    )
