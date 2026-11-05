#!/usr/bin/env python3
"""PES 2021 PC (1.01.00) pesdb parser: Player / Team / PlayerAssignment / Country / Stadium / Coach (+ weekly files).

Reads common/etc/pesdb/*.bin straight from the game CPKs (dt80_100E update pack overrides dt10, then dt00),
unwraps the WESYS container (ff 10 81 'WESYS' + u32 csize + u32 usize + zlib; flag byte 0x00 = stored),
decodes the records and can recompute the CHECK_CHEAT param_check_sum / entry_check_sum.

usage:
  pesdb.py checksum                      param_check_sum + entry_check_sum (compare with CMD_CHECK_CHEAT logs)
  pesdb.py player <id|name substring>    print decoded player(s)
  pesdb.py team <id|name substring>      print team + roster
  pesdb.py dump <out.json> [--raw]       players + teams (+countries/stadiums/coaches) as JSON

Field map derived statically from PES2021.exe (see ../db.md): unpacker 0x1414c78e0, record convert 0x1414f8420,
param getter 0x1414c66c0 (108 params), team build 0x1414f5120, checksums 0x141128e90 / 0x141129240.
Read-only: never writes to the game folder.
"""
import json, os, struct, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get('PES_GAME_DIR') or os.path.abspath(os.path.join(HERE, '..', '..', '..', '..', '..'))  # folder holding game/
sys.path.insert(0, HERE)
import cpk  # noqa: E402  (tools/cpk.py next to this file)

CPKS = [os.path.join(REPO, 'game/files/download/dt80_100E_x64.cpk'),
        os.path.join(REPO, 'game/files/Data/dt10_x64.cpk'),
        os.path.join(REPO, 'game/files/Data/dt00_x64.cpk')]

# ---------------------------------------------------------------- container / CPK
_TOC = {}


def unwrap(d):
    if len(d) >= 16 and d[:2] == b'\xff\x10' and d[3:8] == b'WESYS':
        cs, us = struct.unpack_from('<II', d, 8)
        if d[2] == 0x81:
            out = zlib.decompress(d[16:16 + cs])
        else:  # 0x00: stored
            out = d[16:16 + us]
        assert len(out) == us, (len(out), us)
        return out
    return d


def load(name, cpks=CPKS):
    """returns (bytes, cpk_path) of common/etc/pesdb/<name>, first CPK that has it wins."""
    for c in cpks:
        if not os.path.exists(c):
            continue
        if c not in _TOC:
            _TOC[c] = {cpk.name(r): r for r in cpk.toc(c)[1]}
        r = _TOC[c].get('common/etc/pesdb/' + name)
        if r is not None:
            with open(c, 'rb') as f:
                f.seek(r['_abs'])
                return unwrap(f.read(r['FileSize'])), c
    return None, None


def cstr(b):
    return b.split(b'\0', 1)[0].decode('utf-8', 'replace')


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def bits(b, o, s, w):
    return (u32(b, o) >> s) & ((1 << w) - 1)


# ---------------------------------------------------------------- Player.bin (312-byte records)
POSITIONS = ['GK', 'CB', 'LB', 'RB', 'DMF', 'CMF', 'LMF', 'RMF', 'AMF', 'LWF', 'RWF', 'SS', 'CF']
# param index -> (file dword offset, shift, width, add)  [static: bit-probed emulation of unpacker 0x1414c78e0]
PMAP = [(52,18,4,0),(56,22,2,0),(56,30,2,0),(56,24,2,0),(56,28,2,0),(56,16,2,0),(48,30,2,0),(56,8,2,0),(56,18,2,0),
        (56,12,2,0),(56,20,2,0),(36,30,2,0),(56,26,2,0),(40,30,2,0),(44,18,6,40),(32,19,6,40),(40,6,6,40),(44,0,6,40),
        (32,25,6,40),(52,0,6,40),(48,12,6,40),(32,7,6,40),(48,18,6,40),(36,0,6,40),(36,24,6,40),(48,0,6,40),
        (28,26,6,40),(40,12,6,40),(44,12,6,40),(32,13,6,40),(40,0,6,40),(36,12,6,40),(56,14,2,0),(36,18,6,40),
        (48,6,6,40),(44,24,6,40),(44,6,6,40),(40,24,6,40),(36,6,6,40),(40,18,6,40),(52,22,3,0),(56,10,2,0),
        (64,2,1,0),(60,29,1,0),(56,6,2,0),(48,24,6,15),(24,24,8,100),(32,0,7,30),(56,3,3,0),(44,30,2,0),(60,0,2,0),
        (52,25,3,0),(52,10,4,0),(56,0,3,0),(52,14,4,0),(12,27,5,0),(52,28,3,0),(52,6,4,0),(28,18,8,0),(16,27,5,0),
        (60,9,1,0),(64,19,1,0),(64,14,1,0),(60,30,1,0),(64,17,1,0),(52,31,1,0),(64,8,1,0),(64,7,1,0),(60,5,1,0),
        (60,17,1,0),(60,2,1,0),(64,16,1,0),(64,13,1,0),(60,15,1,0),(64,15,1,0),(64,1,1,0),(64,12,1,0),(60,25,1,0),
        (60,31,1,0),(60,27,1,0),(60,4,1,0),(60,3,1,0),(60,13,1,0),(64,3,1,0),(60,8,1,0),(60,10,1,0),(64,9,1,0),
        (64,10,1,0),(60,11,1,0),(60,24,1,0),(64,5,1,0),(64,18,1,0),(60,12,1,0),(64,4,1,0),(60,6,1,0),(64,0,1,0),
        (60,21,1,0),(60,22,1,0),(60,26,1,0),(60,20,1,0),(60,14,1,0),(60,19,1,0),(60,16,1,0),(60,23,1,0),(64,11,1,0),
        (32,31,1,0),(64,6,1,0),(60,7,1,0)]

PNAMES = {0: 'registered_position'}
for _i, _p in enumerate(['CF', 'SS', 'LWF', 'RWF', 'AMF', 'DMF', 'CMF', 'LMF', 'RMF', 'CB', 'LB', 'RB', 'GK'], 1):
    PNAMES[_i] = 'pos_' + _p  # 0 = cannot, 1 = secondary (B), 2 = proficient (A)
PNAMES.update({14: 'offensive_awareness', 15: 'defensive_awareness', 16: 'gk_awareness', 17: 'dribbling',
               18: 'ball_control', 19: 'tight_possession', 20: 'finishing', 21: 'low_pass', 22: 'lofted_pass',
               23: 'heading', 24: 'ball_winning', 25: 'aggression', 26: 'place_kicking', 27: 'curl',
               28: 'gk_catching', 29: 'gk_parrying', 30: 'gk_reflexes', 31: 'gk_reach', 32: 'weak_foot_accuracy',
               33: 'speed', 34: 'physical_contact', 35: 'balance', 36: 'kicking_power', 37: 'acceleration',
               38: 'jump', 39: 'stamina', 40: 'form', 41: 'injury_resistance', 42: 'stronger_foot_left',
               43: 'flag43', 44: 'weak_foot_usage', 45: 'age', 46: 'height', 47: 'weight', 48: 'star48',
               49: 'unk49', 50: 'unk50', 51: 'dribble_hunching', 52: 'dribble_arm_movement',
               53: 'running_hunching', 54: 'running_arm_movement', 55: 'free_kick_motion',
               56: 'penalty_kick_motion', 57: 'corner_kick_motion', 58: 'goal_celebration',
               59: 'playing_style'})
COM_STYLES = {60: 'Trickster', 61: 'Mazing Run', 62: 'Speeding Bullet', 63: 'Incisive Run',
              64: 'Long Ball Expert', 65: 'Early Cross', 66: 'Long Ranger'}
SKILL_UI_ORDER = [67, 104, 68, 69, 70, 105, 71, 72, 99, 73, 74, 98, 106, 75, 100, 101, 76, 77, 78, 79, 107, 80, 81,
                  82, 83, 95, 84, 85, 102, 86, 87, 96, 97, 88, 89, 90, 103, 91, 92, 93, 94]  # table 0x142979100
SKILL_NAMES = ['Scissors Feint', 'Double Touch', 'Flip Flap', 'Marseille Turn', 'Sombrero', 'Chop Turn',
               'Cut Behind & Turn', 'Scotch Move', 'Sole Control', 'Heading', 'Long Range Curler',
               'Chip Shot Control', 'Long Range Drive', 'Knuckle Shot', 'Dipping Shot', 'Rising Shot',
               'Acrobatic Finishing', 'Heel Trick', 'First-time Shot', 'One-touch Pass', 'Through Passing',
               'Weighted Pass', 'Pinpoint Crossing', 'Outside Curler', 'Rabona', 'No Look Pass', 'Low Lofted Pass',
               'GK Low Punt', 'GK High Punt', 'Long Throw', 'GK Long Throw', 'Penalty Specialist',
               'GK Penalty Saver', 'Gamesmanship', 'Man Marking', 'Track Back', 'Interception', 'Acrobatic Clear',
               'Captaincy', 'Super-sub', 'Fighting Spirit']
SKILLS = dict(zip(SKILL_UI_ORDER, SKILL_NAMES))
for _k, _v in COM_STYLES.items():
    PNAMES[_k] = 'com_' + _v.lower().replace(' ', '_')
for _k, _v in SKILLS.items():
    PNAMES[_k] = 'skill_' + _v.lower().replace(' ', '_').replace('&', 'and').replace('-', '_')
# stored playing-style value -> name (stored order; UI order via table 0x142979080)
PLAYING_STYLES = ['None', 'Goal Poacher', 'Dummy Runner', 'Fox in the Box', 'Prolific Winger', 'Classic No. 10',
                  'Hole Player', 'Box-to-Box', 'Anchor Man', 'The Destroyer', 'Extra Frontman', 'Offensive Full-back',
                  'Defensive Full-back', 'Target Man', 'Creative Playmaker', 'Build Up', 'Offensive Goalkeeper',
                  'Defensive Goalkeeper', 'Roaming Flank', 'Cross Specialist', 'Orchestrator', 'Full-back Finisher']
ABILITY_UI_ORDER = [14, 18, 17, 19, 21, 22, 20, 23, 26, 27, 33, 37, 36, 38, 34, 35, 39, 15, 24, 25, 16, 28, 29, 30, 31,
                    44, 32, 40, 41]  # table 0x142978fd0 (player sheet order)


def player_params(r):
    return [((u32(r, o) >> s) & ((1 << w) - 1)) + a for (o, s, w, a) in PMAP]


def date8(v):
    v &= 0x7ffffff
    return f'{v // 10000:04d}-{v // 100 % 100:02d}-{v % 100:02d}' if v else None


def decode_player(r):
    p = player_params(r)
    pid = u32(r, 8)
    d = {
        'id': pid,
        'name': cstr(r[0xfb:0x138]),          # international name (Latin)
        'name_jp': cstr(r[0x44:0x81]),        # Japanese name (used when game language == 5)
        'shirt_name': cstr(r[0x81:0xbe]),     # -> rec+0x75
        'shirt_name2': cstr(r[0xbe:0xfb]),    # -> rec+0xb2 (usually identical; commentary/print name?)
        'nationality': bits(r, 0x1c, 9, 9),   # Country id (Country.bin u32@0 >> 10 & 0x1ff)
        'nationality2': bits(r, 0x1c, 0, 9) or None,  # [inferred] second nationality
        'assoc_club': u32(r, 0) or None,      # [inferred] associated / original club (legends etc.), NOT current team
        'loan_from': u32(r, 4) or None,       # parent club while on loan
        'contract_until': date8(u32(r, 0xc)),
        'loan_until': date8(u32(r, 0x10)),
        'value_f14': u32(r, 0x14) & 0xffffff,  # [guess] market value/salary (k)
        'byte17': r[0x17],
        'value_f18': u32(r, 0x18) & 0xffffff,
        'age': p[45], 'height': p[46], 'weight': p[47],
        'foot': 'L' if p[42] else 'R',
        'registered_position': POSITIONS[p[0]] if p[0] < 13 else p[0],
        'positions': {POSITIONS[(13, 10, 11, 12, 6, 7, 8, 9, 5, 3, 4, 2, 1).index(k)]: p[k]
                      for k in range(1, 14) if p[k]},
        'playing_style': PLAYING_STYLES[p[59]] if p[59] < len(PLAYING_STYLES) else p[59],
        'skills': [SKILLS[k] for k in SKILL_UI_ORDER if p[k]],
        'com_styles': [COM_STYLES[k] for k in range(60, 67) if p[k]],
        'abilities': {PNAMES[k]: p[k] for k in ABILITY_UI_ORDER},
        'params': p,  # all 108 in getter-index order (index i == accessor 0x1414c66c0 case i)
    }
    # display scale for 1..N stats
    for k in ('weak_foot_usage', 'weak_foot_accuracy', 'form', 'injury_resistance'):
        d['abilities'][k] += 1
    return d


# ---------------------------------------------------------------- Team.bin (0x5fc-byte records)
TEAM_NAME_OFFS = {'jp': 0x58, 'es': 0x9e, 'it?': 0xe4, 'el': 0x12a, 'en': 0x170, 'es2?': 0x1b6, 'fr': 0x1fc,
                  'tr': 0x242, 'pt?': 0x288, 'x2ce': 0x2ce, 'de': 0x32c, 'x37c': 0x37c, 'zh': 0x3c2,
                  'x408': 0x408, 'x44e': 0x44e, 'x494': 0x494, 'ru': 0x4da, 'nl': 0x520, 'x570': 0x570, 'x5b6': 0x5b6}


def decode_team(r):
    names = {k: cstr(r[o:o + 0x46]) for k, o in TEAM_NAME_OFFS.items()}
    return {
        'id': u32(r, 8),
        'name': names['en'] or names['jp'],
        'names': {k: v for k, v in names.items() if v},
        'short': cstr(r[0x372:0x37c]),
        'short2': cstr(r[0x566:0x570]) or None,
        'coach_id': u32(r, 0) or None,
        'stadium_id': struct.unpack_from('<H', r, 0x3c)[0],
        'country': bits(r, 0x44, 18, 9),
        'rivals': [u32(r, o) for o in (0x1c, 0x30, 0x14, 0x28, 0x0c, 0x10, 0x18, 0x24, 0x2c) if u32(r, o)],
        'kind': bits(r, 0x4c, 28, 4),   # [inferred] team kind (club/national/...) via table 0x14297d090
    }


# ---------------------------------------------------------------- PlayerAssignment.bin (16 bytes)
def decode_assignments(d):
    out = []
    for o in range(0, len(d) - 15, 16):
        eid, pid, tid, f = struct.unpack_from('<IIII', d, o)
        out.append({'entry': eid, 'player': pid, 'team': tid, 'shirt': (f & 0x3ff) + 1, 'order': (f >> 10) & 0x3f,
                    'captain': (f >> 21) & 1,
                    # set-piece bits; team slots 0..5 use bits [19,17,17,18,16,20] (0x1414f06a0) [names guessed]
                    'roles': {'b16': (f >> 16) & 1, 'b17': (f >> 17) & 1, 'b18': (f >> 18) & 1,
                              'b19': (f >> 19) & 1, 'b20': (f >> 20) & 1}})
    return out


# ---------------------------------------------------------------- small tables
def decode_countries(d):
    R = 0x58c
    out = {}
    for o in range(0, len(d) - R + 1, R):
        r = d[o:o + R]
        out[(u32(r, 0) >> 10) & 0x1ff] = {'name': cstr(r[0x120:0x166]), 'name_jp': cstr(r[0x4e:0x94]),
                                          'short': cstr(r[0x2c4:0x2ce]), 'region': u32(r, 4)}
    return out


def decode_stadiums(d):
    R = 0x188
    return {struct.unpack_from('<H', d, o + 4)[0]: {'name': cstr(d[o + 0xbd:o + 0x172]),
                                                    'name_jp': cstr(d[o + 8:o + 0xbd]),
                                                    'label': cstr(d[o + 0x172:o + R])}
            for o in range(0, len(d) - R + 1, R)}


def decode_coaches(d):
    R = 100
    return {u32(d, o): {'name': cstr(d[o + 0x36:o + 0x64]), 'name_jp': cstr(d[o + 8:o + 0x36]), 'f4': u32(d, o + 4)}
            for o in range(0, len(d) - R + 1, R)}


def decode_weekly(d, rs):
    """PlayerWeekly (12 B: u32 player id, u32 ?, u8 condition@+8) / TeamWeekly (8 B: u32 team id, u8 @+4)."""
    return [(u32(d, o), d[o + rs - 4] & 7) for o in range(0, len(d) - rs + 1, rs)]


# ---------------------------------------------------------------- database
class DB:
    def __init__(self, cpks=CPKS):
        self.src = {}

        def ld(n):
            d, c = load(n, cpks)
            self.src[n] = os.path.basename(c) if c else None
            return d
        self.player_raw = ld('Player.bin')
        self.team_raw = ld('Team.bin')
        self.pa_raw = ld('PlayerAssignment.bin')
        self.players = [decode_player(self.player_raw[o:o + 312]) for o in range(0, len(self.player_raw), 312)]
        self.teams = [decode_team(self.team_raw[o:o + 0x5fc]) for o in range(0, len(self.team_raw), 0x5fc)]
        self.assign = decode_assignments(self.pa_raw)
        self.pby = {p['id']: p for p in self.players}
        self.tby = {t['id']: t for t in self.teams}
        for t in self.teams:
            t['roster'] = []
        for p in self.players:
            p['teams'] = []
        for a in self.assign:
            t = self.tby.get(a['team'])
            if t is not None:
                t['roster'].append(a)
            p = self.pby.get(a['player'])
            if p is not None:
                p['teams'].append({'team': a['team'], 'team_name': t['name'] if t else None, 'shirt': a['shirt'],
                                   'captain': a['captain']})
        for t in self.teams:
            t['roster'].sort(key=lambda a: a['order'])
        c = ld('Country.bin'); s = ld('Stadium.bin'); co = ld('Coach.bin')
        self.countries = decode_countries(c) if c else {}
        self.stadiums = decode_stadiums(s) if s else {}
        self.coaches = decode_coaches(co) if co else {}
        pw = ld('PlayerWeekly.bin'); tw = ld('TeamWeekly.bin')
        self.player_weekly = decode_weekly(pw, 12) if pw else []
        self.team_weekly = decode_weekly(tw, 8) if tw else []

    # CHECK_CHEAT param_check_sum (0x141128e90). live_mode=True emulates DB-manager mode 1 (live data set),
    # where regstate(id) = PlayerWeekly condition (default 2, value 5 -> 2); otherwise regstate = 5.
    def param_check_sum(self, live_mode=False):
        KEY = (0x29bda03d, 0x0593fbe2, 0x298015ab, 0x9fbead21)
        cond = {}
        for pid, c in self.player_weekly:
            cond.setdefault(pid, c)
        acc = [0, 0, 0, 0]
        for p in self.players:
            pid16 = p['id'] & 0xffff
            if not pid16:
                continue
            pr = p['params']
            if live_mode:
                c = cond.get(pid16, 2)
                reg = 2 if c == 5 else c
            else:
                reg = 5
            s = pid16 + sum(pr) + (pr[46] & 0xff) + (pr[47] & 0xff) + reg
            acc[pid16 & 3] = (acc[pid16 & 3] + s) & 0xffffffff
        return ''.join('%08X' % (a ^ k) for a, k in zip(acc, KEY))

    # CHECK_CHEAT entry_check_sum (0x141129240): per team (teamId&0xffff) + sum(playerId + shirtNumber), <=40 slots
    def entry_check_sum(self):
        acc = [0, 0, 0, 0]
        for t in self.teams:
            tid = t['id'] & 0xffff
            s = tid
            for a in t['roster'][:40]:
                if a['player']:
                    s += a['player'] + a['shirt']
            acc[tid & 3] = (acc[tid & 3] + s) & 0xffffffff
        return ''.join('%08X' % a for a in acc)

    def team_name(self, tid):
        t = self.tby.get(tid)
        return t['name'] if t else None


def find(items, q, key='name'):
    if q.isdigit():
        return [x for x in items if x['id'] == int(q)]
    return [x for x in items if q.upper() in (x[key] or '').upper()]


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    db = DB()
    if cmd == 'checksum':
        print('sources:', db.src)
        print('players', len(db.players), 'teams', len(db.teams), 'assignments', len(db.assign))
        print('param_check_sum', db.param_check_sum())
        print('param_check_sum(live mode 1, PlayerWeekly applied)', db.param_check_sum(live_mode=True))
        print('entry_check_sum', db.entry_check_sum())
    elif cmd == 'player':
        for p in find(db.players, sys.argv[2]):
            q = dict(p); q.pop('params')
            q['nationality'] = (q['nationality'], db.countries.get(q['nationality'], {}).get('name'))
            if q['assoc_club']:
                q['assoc_club'] = (q['assoc_club'], db.team_name(q['assoc_club']))
            if q['loan_from']:
                q['loan_from'] = (q['loan_from'], db.team_name(q['loan_from']))
            print(json.dumps(q, ensure_ascii=False))
    elif cmd == 'team':
        for t in find(db.teams, sys.argv[2]):
            print(t['id'], t['name'], t['short'], 'country', db.countries.get(t['country'], {}).get('name'),
                  'coach', db.coaches.get(t['coach_id'], {}).get('name'),
                  'stadium', db.stadiums.get(t['stadium_id'], {}).get('name'),
                  'rivals', [db.team_name(x) for x in t['rivals']])
            for a in t['roster']:
                p = db.pby.get(a['player'])
                print(f"   #{a['shirt']:<3} {p['name'] if p else a['player']:<24} {p['registered_position'] if p else ''}"
                      f"{'  (C)' if a['captain'] else ''}")
    elif cmd == 'dump':
        out = sys.argv[2]
        raw = '--raw' in sys.argv
        players = []
        for p in db.players:
            q = dict(p)
            if not raw:
                q['params'] = {PNAMES.get(i, f'p{i}'): v for i, v in enumerate(p['params'])}
            players.append(q)
        teams = [{k: v for k, v in t.items()} for t in db.teams]
        json.dump({'sources': db.src, 'param_check_sum': db.param_check_sum(),
                   'entry_check_sum': db.entry_check_sum(),
                   'param_names': [PNAMES.get(i, f'p{i}') for i in range(108)],
                   'players': players, 'teams': teams,
                   'countries': db.countries, 'stadiums': db.stadiums, 'coaches': db.coaches},
                  open(out, 'w'), ensure_ascii=False)
        print('wrote', out, len(players), 'players', len(teams), 'teams')
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
