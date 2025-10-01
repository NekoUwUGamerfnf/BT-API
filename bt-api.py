import os
import sqlite3
from flask import Flask, request, jsonify, render_template_string, Response
import requests
import time
from waitress import serve

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'db')
TOKENS_PATH = os.path.join(BASE_DIR, 'valid_tokens.json')
FAKE_GOOD_CODE_FOR_ALL_TOKENS = True

if not os.path.exists(TOKENS_PATH):
    with open(TOKENS_PATH, 'w') as f:
        import json
        json.dump([], f)

with open(TOKENS_PATH) as f:
    import json
    valid_tokens = json.load(f)

def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def store_kill(values):
    if not values or not isinstance(values, dict):
        return
    fields = ['attacker_id', 'victim_id', 'attacker_name', 'victim_name']
    for field in fields:
        if field not in values or not values[field]:
            return
        if not str(values[field]).strip():
            return
    attacker_id = str(values['attacker_id']).strip()
    victim_id = str(values['victim_id']).strip()
    if attacker_id == "":
        attacker_id = str(values['attacker_uid']).strip()
    if victim_id == "":
        victim_id = str(values['victim_uid']).strip()
    attacker_name = str(values['attacker_name']).strip()
    victim_name = str(values['victim_name']).strip()
    current_time = time.time()
    server_id = values.get('server_id')
    if server_id:
        server_id = str(server_id).strip()
        if not server_id or len(server_id) > 30:
            return
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute('INSERT OR IGNORE INTO user_stats (player_id, player_name, kills, deaths) VALUES (?, ?, 0, 0)', (attacker_id, attacker_name))
    cur.execute('INSERT OR IGNORE INTO user_stats (player_id, player_name, kills, deaths) VALUES (?, ?, 0, 0)', (victim_id, victim_name))
    cur.execute('UPDATE user_stats SET kills = kills + 1 WHERE player_id=?', (attacker_id,))
    cur.execute('UPDATE user_stats SET deaths = deaths + 1 WHERE player_id=?', (victim_id,))
    if server_id:
        cur.execute('INSERT OR IGNORE INTO servers (server_id) VALUES (?)', (server_id,))
        cur.execute('INSERT OR IGNORE INTO server_kills (player_id, server_id, kills) VALUES (?, ?, 0)', (attacker_id, server_id))
        cur.execute('UPDATE server_kills SET kills = kills + 1 WHERE player_id=? AND server_id=?', (attacker_id, server_id))
        cur.execute('INSERT OR IGNORE INTO server_deaths (player_id, server_id, deaths) VALUES (?, ?, 0)', (victim_id, server_id))
        cur.execute('UPDATE server_deaths SET deaths = deaths + 1 WHERE player_id=? AND server_id=?', (victim_id, server_id))
    for user_id, name in [(attacker_id, attacker_name), (victim_id, victim_name)]:
        cur.execute('INSERT OR IGNORE INTO user_aliases (player_id, name, timestamp) VALUES (?, ?, ?)', (user_id, name, current_time))
    cur.execute('SELECT timestamp, name FROM user_aliases WHERE player_id=? ORDER BY timestamp DESC LIMIT 1', (attacker_id,))
    attacker_latest = cur.fetchone()
    if attacker_latest:
        cur.execute('UPDATE user_stats SET player_name=? WHERE player_id=?', (attacker_latest['name'], attacker_id))
    cur.execute('SELECT timestamp, name FROM user_aliases WHERE player_id=? ORDER BY timestamp DESC LIMIT 1', (victim_id,))
    victim_latest = cur.fetchone()
    if victim_latest:
        cur.execute('UPDATE user_stats SET player_name=? WHERE player_id=?', (victim_latest['name'], victim_id))
    conn.commit()
    conn.close()

@app.route('/data', methods=['POST'])
def kill_endpoint():
    token = request.headers.get('Token')
    if token not in valid_tokens:
        if FAKE_GOOD_CODE_FOR_ALL_TOKENS:
            return jsonify({'status': 'ok'}), 200
        else:
            return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json()
    store_kill(data)
    return jsonify({'status': 'ok'}), 200

@app.route('/players/<identifier>', methods=['GET'])
def get_stats(identifier):
    server = request.args.get('server_id')
    name_or_id = identifier.lower()
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute('SELECT player_id, player_name FROM user_stats')
    all_players = cur.fetchall()
    player_id = None
    for row in all_players:
        if row['player_name'].lower() == name_or_id:
            player_id = row['player_id']
            break
    if not player_id:
        cur.execute('SELECT player_id, name FROM user_aliases')
        for row in cur.fetchall():
            if row['name'].lower() == name_or_id:
                player_id = row['player_id']
                break
    if not player_id:
        for row in all_players:
            if row['player_name'].lower().startswith(name_or_id):
                player_id = row['player_id']
                break
    if not player_id:
        cur.execute('SELECT player_id, name FROM user_aliases')
        for row in cur.fetchall():
            if row['name'].lower().startswith(name_or_id):
                player_id = row['player_id']
                break
    if not player_id:
        player_id = str(identifier)
    cur.execute('SELECT player_name, kills, deaths FROM user_stats WHERE player_id=?', (player_id,))
    stat = cur.fetchone()
    if not stat:
        conn.close()
        return jsonify({'error': 'player not found'}), 404
    if server:
        cur.execute('SELECT kills FROM server_kills WHERE player_id=? AND server_id=?', (player_id, server))
        kills_row = cur.fetchone()
        kills = kills_row['kills'] if kills_row else 0
        cur.execute('SELECT deaths FROM server_deaths WHERE player_id=? AND server_id=?', (player_id, server))
        deaths_row = cur.fetchone()
        deaths = deaths_row['deaths'] if deaths_row else 0
    else:
        kills = stat['kills']
        deaths = stat['deaths']
    cur.execute('SELECT name FROM user_aliases WHERE player_id=? ORDER BY timestamp DESC', (player_id,))
    aliases_all = [row['name'] for row in cur.fetchall()]
    latest_name = aliases_all[0] if aliases_all else stat['player_name']
    aliases = aliases_all[1:] if len(aliases_all) > 1 else []
    kd = kills / deaths if deaths > 0 else float(kills)
    resp = {
        'name': latest_name,
        'aliases': aliases,
        'uid': player_id,
        'kills': kills,
        'deaths': deaths,
        'kd': kd
    }
    if server:
        resp['server_id'] = server
    conn.close()
    return jsonify(resp), 200

@app.route('/top', methods=['GET'])
def top_players():
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
        if page < 1: page = 1
        if per_page < 1 or per_page > 100: per_page = 10
    except:
        page = 1
        per_page = 10
    offset = (page - 1) * per_page
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute('SELECT player_id, player_name, kills, deaths FROM user_stats WHERE kills>0 OR deaths>0')
    all_stats = cur.fetchall()
    players = []
    for row in all_stats:
        cur.execute('SELECT name FROM user_aliases WHERE player_id=? ORDER BY timestamp DESC', (row['player_id'],))
        aliases_all = [ar['name'] for ar in cur.fetchall()]
        aliases = aliases_all[1:] if len(aliases_all) > 1 else []
        kd = row['kills'] / row['deaths'] if row['deaths'] > 0 else float(row['kills'])
        players.append({
            'name': row['player_name'],
            'uid': row['player_id'],
            'kills': row['kills'],
            'deaths': row['deaths'],
            'kd': kd,
            'aliases': aliases
        })
    players = sorted(players, key=lambda x: (x['kd'], x['kills']), reverse=True)
    total_players = len(players)
    total_pages = (total_players + per_page - 1) // per_page
    page_players = players[offset:offset+per_page]
    conn.close()
    return jsonify({
        "players": page_players,
        "page": page,
        "per_page": per_page,
        "total_players": total_players,
        "total_pages": total_pages
    }), 200

@app.route('/favicon.ico', methods=['GET'])
def favicon():
    url = "https://static.wikia.nocookie.net/titanfall/images/1/15/Titanfall_bt_7274.webp/revision/latest?cb=20241203221703"
    resp = requests.get(url)
    return Response(resp.content, mimetype='image/webp')

@app.route('/', methods=['GET'])
def webui():
    html = """
    <!doctype html>
    <html>
    <head>
        <title>BT Player Stats</title>
        <style>
        body { font-family: Arial, sans-serif; padding: 2em; background: #181818; color: #eee; font-size: 1.25em; }
        input, button { margin: 0.3em 0.3em 0.3em 0; font-size: 1.1em; padding: 0.3em 0.5em; }
        #stats { margin-top: 1.5em; }
        #leaderboard { margin-top: 2.5em; }
        .error { color: #ff7575; font-size: 1.1em; }
        .kdp { font-size: 1.3em; font-family: monospace; }
        table { border-collapse: collapse; width: auto; min-width: 0; background: #232323; font-size: 1.1em; }
        th, td { padding: 0.35em 0.7em; border: 1px solid #333; text-align: left; font-size: 1em; }
        th { background: #222; font-size: 1.1em; }
        .pagination { margin: 0.3em 0; }
        .pagination button { background: #232323; color: #eee; border: 1px solid #444; padding: 0 6px; margin-right: 0.2em; cursor: pointer; font-size: 0.85em; height: 1.5em; min-width: unset; }
        .pagination button:disabled { color: #666; border-color: #333; cursor: default; }
        .pagination input[type=number] { width: 2.2em; padding: 0 2px; font-size: 1em; background: #232323; color: #eee; border: 1px solid #444; text-align: center; height: 1.5em; }
        </style>
    </head>
    <body>
        <h2>BT Player Stats</h2>
        <div id="server-list" style="float:right;width:200px;margin:0 0 1em 1em;">
            <h3 style="font-size:1em;">Servers</h3>
            <ul id="servers" style="list-style:none;padding:0;margin:0;font-size:0.9em;">Loading...</ul>
        </div>
        <form id="player-form" onsubmit="return false;">
            <label>Player Name Or UID: <input type="text" id="player" required></label>
            <label>Server Id (Optional): <input type="text" id="server"></label>
            <button onclick="lookup()">Lookup</button>
        </form>
        <div id="stats"></div>
        <div id="leaderboard">
            <h3 style="margin-bottom:0.15em;font-size:1em;">Top Players By Kd</h3>
            <div style="margin-bottom:0.15em; display: flex; align-items: center; gap: 0.3em;">
                <span id="top-arrows" style="display:flex;align-items:center;gap:0.1em;"></span>
                <label style="font-size:0.9em; display: flex; align-items: center;">
                    <span style="margin-right:2px;">Per Page:</span>
                    <input type="number" id="top-perpage" min="1" max="100" value="10" style="width:3.0em;padding:0 1px;font-size:0.85em;height:1.2em;">
                    <type="number" id="top-page" min="1" value="1">
                </label>
                <span id="top-pageinfo" style="margin-left:0.5em;font-size:0.92em;"></span>
            </div>
            <div id="top-table">Loading...</div>
            <div class="pagination" id="top-pagination" style="display:none"></div>
        </div>
        <script>
        async function lookup() {
            const name = document.getElementById('player').value.trim();
            const server = document.getElementById('server').value.trim();
            const box = document.getElementById('stats');
            if(!name) { box.innerHTML = '<div class="error">Please Provide A Player Name Or UID</div>'; return; }
            box.innerHTML = "Loading";
            let url = '/players/' + encodeURIComponent(name);
            if (server) url += '?server_id=' + encodeURIComponent(server);
            const resp = await fetch(url);
            if (resp.ok) {
                const j = await resp.json();
                box.innerHTML = `<div>
                    <b>${j.name}</b> (UID: ${j.uid})<br>
                    ${j.aliases && j.aliases.length ? `<div>Aliases: ${j.aliases.join(', ')}</div>` : ''}
                    <div class="kdp">
                     Kills: ${j.kills} | Deaths: ${j.deaths} | Kd: ${j.kd.toFixed(2)}
                     ${j.server_id ? `<br>Server: <code>${j.server_id}</code>` : ""}
                 </div>
             </div>`;
            } else {
                try {
                    const j = await resp.json();
                    box.innerHTML = `<div class="error">${j.error.charAt(0).toUpperCase() + j.error.slice(1).replace(/_/g, ' ')}</div>`;
                } catch {
                    box.innerHTML = "<div class='error'>Unknown Error</div>";
                }
            }
        }

        let topPage = 1;
        let topPerPage = 10;
        let topTotalPages = 1;

        async function loadTop(page = 1, perPage = 10) {
            const tableDiv = document.getElementById('top-table');
            const arrowsDiv = document.getElementById('top-arrows');
            const pageInput = document.getElementById('top-page');
            const perPageInput = document.getElementById('top-perpage');
            const pageInfo = document.getElementById('top-pageinfo');

            perPage = parseInt(perPageInput.value) || 10;
            if (perPage < 1) perPage = 1;
            if (perPage > 100) perPage = 100;

            let pagHtml = "";
            pagHtml += `<button onclick="loadTop(1,topPerPage)" ${topPage==1?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="First">&lt;&lt;</button>`;
            pagHtml += `<button onclick="loadTop(${topPage-1},topPerPage)" ${topPage==1?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Prev">&lt;</button>`;
            pagHtml += `<span style="margin:0 0.3em;">Page ${topPage} Of ${topTotalPages}</span>`;
            pagHtml += `<button onclick="loadTop(${topPage+1},topPerPage)" ${topPage==topTotalPages?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Next">&gt;</button>`;
            pagHtml += `<button onclick="loadTop(${topTotalPages},topPerPage)" ${topPage==topTotalPages?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Last">&gt;&gt;</button>`;
            arrowsDiv.innerHTML = pagHtml;
            pageInfo.textContent = "";

            tableDiv.innerHTML = "Loading";

            try {
                const resp = await fetch(`/top?page=${page}&per_page=${perPage}`);
                if (!resp.ok) throw new Error();
                const data = await resp.json();

                if (data.total_pages > 0 && page > data.total_pages) {
                    loadTop(data.total_pages, perPage);
                    return;
                }

                const players = data.players;
                if (!players.length) {
                    if (data.total_players === 0) {
                        tableDiv.innerHTML = "<i>No Players Found</i>";
                    } else {
                        tableDiv.innerHTML = "<i>No Players On This Page</i>";
                    }
                    let pagHtml = "";
                    pagHtml += `<button onclick="loadTop(1,topPerPage)" ${data.page==1?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="First">&lt;&lt;</button>`;
                    pagHtml += `<button onclick="loadTop(${data.page-1},topPerPage)" ${data.page==1?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Prev">&lt;</button>`;
                    pagHtml += `<span style="margin:0 0.3em;">Page ${data.page} Of ${data.total_pages}</span>`;
                    pagHtml += `<button onclick="loadTop(${data.page+1},topPerPage)" ${data.page==data.total_pages?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Next">&gt;</button>`;
                    pagHtml += `<button onclick="loadTop(${data.total_pages},topPerPage)" ${data.page==data.total_pages?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Last">&gt;&gt;</button>`;
                    arrowsDiv.innerHTML = pagHtml;
                    pageInfo.textContent = "";
                    topPage = data.page;
                    topPerPage = data.per_page;
                    topTotalPages = data.total_pages;
                    return;
                }
                let html = `<table>
                    <tr><th>#</th><th>Name</th><th>Kills</th><th>Deaths</th><th>Kd</th><th>Aliases</th></tr>`;
                players.forEach((p, i) => {
                    html += `<tr>
                        <td>${(data.per_page * (data.page-1)) + i + 1}</td>
                        <td>${p.name} <span style="color:#888;font-size:0.9em;">(${p.uid})</span></td>
                        <td>${p.kills}</td>
                        <td>${p.deaths}</td>
                        <td>${parseFloat(p.kd).toFixed(2)}</td>
                        <td>${p.aliases ? p.aliases.join(', ') : ''}</td>
                    </tr>`;
                });
                html += "</table>";
                tableDiv.innerHTML = html;

                let pagHtml = "";
                pagHtml += `<button onclick="loadTop(1,topPerPage)" ${data.page==1?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="First">&lt;&lt;</button>`;
                pagHtml += `<button onclick="loadTop(${data.page-1},topPerPage)" ${data.page==1?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Prev">&lt;</button>`;
                pagHtml += `<span style="margin:0 0.3em;">Page ${data.page} Of ${data.total_pages}</span>`;
                pagHtml += `<button onclick="loadTop(${data.page+1},topPerPage)" ${data.page==data.total_pages?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Next">&gt;</button>`;
                pagHtml += `<button onclick="loadTop(${data.total_pages},topPerPage)" ${data.page==data.total_pages?'disabled':''} style="background: #232323; color: #eee; border: 1px solid #444; padding: 0 2px; margin-right: 0.1em; cursor: pointer; font-size: 0.85em; height: 1.1em; min-width: unset;" title="Last">&gt;&gt;</button>`;
                arrowsDiv.innerHTML = pagHtml;

                pageInfo.textContent = "";

                pageInput.value = data.page;
                pageInput.max = data.total_pages;
                perPageInput.value = data.per_page;
                topPage = data.page;
                topPerPage = data.per_page;
                topTotalPages = data.total_pages;
            } catch {
                tableDiv.innerHTML = "<div class='error'>Could Not Load Leaderboard</div>";
                pageInfo.textContent = "";
                arrowsDiv.innerHTML = "";
            }
        }

        function applyTopPage() {
            const pageInput = document.getElementById('top-page');
            const perPageInput = document.getElementById('top-perpage');
            let page = parseInt(pageInput.value) || 1;
            let perPage = parseInt(perPageInput.value) || 10;
            if (page < 1) page = 1;
            if (perPage < 1) perPage = 1;
            if (perPage > 100) perPage = 100;
            if (page > topTotalPages) page = topTotalPages;
            loadTop(page, perPage);
        }

        async function loadServers() {
            const ul = document.getElementById('servers');
            ul.innerHTML = '<li>Loading...</li>';
            try {
                const resp = await fetch('/servers');
                if (!resp.ok) throw new Error();
                const data = await resp.json();
                if (data.servers.length === 0) {
                    ul.innerHTML = '<li><i>No Servers</i></li>';
                } else {
                    ul.innerHTML = data.servers
                        .map(id => `<li><code>${id}</code></li>`)
                        .join('');
                }
            } catch {
                ul.innerHTML = '<li><span class="error">Error loading servers</span></li>';
            }
        }

        document.getElementById('top-page').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') applyTopPage();
        });
        document.getElementById('top-perpage').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                let perPage = parseInt(this.value) || 10;
                if (perPage < 1) perPage = 1;
                if (perPage > 100) perPage = 100;
                loadTop(topPage, perPage);
            }
        });
        document.getElementById('top-perpage').addEventListener('input', function() {
            let perPage = parseInt(this.value) || 10;
            if (perPage < 1) perPage = 1;
            if (perPage > 100) perPage = 100;
            loadTop(1, perPage);
        });

        window.onload = () => { loadTop(topPage, topPerPage); loadServers(); };
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route('/servers', methods=['GET'])
def servers():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute('SELECT DISTINCT server_id FROM servers')
    ids = [r['server_id'] for r in cur.fetchall()]
    conn.close()
    return jsonify({'servers': ids}), 200

if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=7274)
