"""Motor estadistico de SensFinder (sin interfaz).

Idea: cada "prueba" mide un rendimiento a una sensibilidad (cm/360). Un proceso
gaussiano modela rendimiento = f(log cm/360) con ruido; despues de cada prueba
elige la siguiente sensibilidad con Expected Improvement, corrige el efecto de
aprendizaje/fatiga, amplia el rango si el optimo cae en un borde y se detiene
cuando la perdida esperada de quedarse con la recomendacion es minima (bootstrap).
"""
import math
import random

LIMIT_LO, LIMIT_HI = 3.0, 250.0
INITIAL_T = [0.5, 0.12, 0.88, 0.3, 0.7]
MIN_TRIALS, MAX_TRIALS = 9, 16
REGRET_STOP = 0.010
PRESETS = {0: (7, 11, 0.020), 1: (9, 16, 0.010), 2: (12, 22, 0.005)}  # rapido / normal / preciso
PRIOR_NOISE_MULT = 8.0  # las sesiones anteriores pesan menos que la actual
ROBUST = True
ZONE = 0.02  # "zona optima": rendimiento a menos de un 2% del maximo
GRID_N = 81

# Catalogo de juegos. yaw = grados por count con sensibilidad 1 (o 1% en Fortnite).
# approx=True: valor no confirmado del todo; la app avisa de que se verifique con un giro de 360 grados.
GAME_LIST = [
    dict(key="valorant", name="Valorant", yaw=0.07, fmt="%.3f", unit="", fov=103),
    dict(key="cs2", name="CS2 / CS:GO", yaw=0.022, fmt="%.3f", unit="", fov=106),
    dict(key="apex", name="Apex Legends", yaw=0.022, fmt="%.3f", unit="", fov=100),
    dict(key="ow2", name="Overwatch 2", yaw=0.0066, fmt="%.2f", unit="", fov=103),
    dict(key="fortnite", name="Fortnite", yaw=0.005555, fmt="%.1f%%", unit="%", fov=80),
    dict(key="cod", name="Call of Duty", yaw=0.0066, fmt="%.2f", unit="", fov=100),
    dict(key="r6", name="Rainbow Six Siege", yaw=0.00572957795, fmt="%.1f", unit="", fov=100),
    dict(key="minecraft", name="Minecraft (Java)", yaw=None, fmt="%d%%", unit="%", fov=102),
    dict(key="roblox", name="Roblox", yaw=0.39783, fmt="%.3f", unit="", fov=102, approx=True),
    dict(key="pubg", name="PUBG", yaw=0.002222, fmt="%.1f", unit="", fov=100, approx=True),
    dict(key="rust", name="Rust", yaw=0.1125, fmt="%.3f", unit="", fov=90, approx=True),
]
GAMES = [(g["name"], g["yaw"]) for g in GAME_LIST[:4]]  # compatibilidad
_MC_MIN, _MC_MAX = 0.0096, 0.6144  # grados por count a 0% y 200%


def _dpc(cm360, dpi):
    return 360.0 / (cm360 * dpi / 2.54)


def game_value(game, cm360, dpi):
    """Valor de sensibilidad del juego para esos cm/360. Devuelve (numero, dentro_del_rango_del_juego)."""
    dpc = _dpc(cm360, dpi)
    if game["key"] == "minecraft":
        f = (dpc / 1.2) ** (1 / 3)
        pct = 200.0 * (f - 0.2) / 0.6
        return pct, _MC_MIN <= dpc <= _MC_MAX
    return dpc / game["yaw"], True


def game_to_cm(game, value, dpi):
    """Inversa: cm/360 que produce una sensibilidad del juego con ese DPI."""
    if value <= 0:
        return 0.0
    if game["key"] == "minecraft":
        dpc = 1.2 * (0.6 * value / 200.0 + 0.2) ** 3
    else:
        dpc = game["yaw"] * value
    return 360.0 * 2.54 / (dpi * dpc)


# ----------------------------------------------------------------- conversiones
def deg_per_count(cm360, dpi):
    return 360.0 / (cm360 * dpi / 2.54)


def game_sens(cm360, dpi, yaw):
    return 360.0 * 2.54 / (cm360 * dpi * yaw)


def flick_score(thr, hits, misses, expired):
    """Throughput de Fitts medio (bits/s, normaliza la dificultad de cada blanco) x precision."""
    if not thr:
        return 0.0
    return mean(thr) * hits / (hits + misses + expired)


def block_score(hits, misses, elapsed):
    """Blancos por segundo x precision."""
    if hits == 0 or elapsed <= 0:
        return 0.0
    return (hits / elapsed) * (hits / (hits + misses))


# ------------------------------------------------------------------ algebra lineal
def mean(v):
    return sum(v) / len(v)


def variance(v):
    m = mean(v)
    return sum((x - m) ** 2 for x in v) / max(1, len(v) - 1)


def solve(a, b):
    n = len(b)
    m = [a[i][:] + [b[i]] for i in range(n)]
    for i in range(n):
        p = max(range(i, n), key=lambda r: abs(m[r][i]))
        m[i], m[p] = m[p], m[i]
        if abs(m[i][i]) < 1e-14:
            m[i][i] = 1e-14
        for r in range(i + 1, n):
            f = m[r][i] / m[i][i]
            for c in range(i, n + 1):
                m[r][c] -= f * m[i][c]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (m[i][n] - sum(m[i][c] * x[c] for c in range(i + 1, n))) / m[i][i]
    return x


def lstsq(rows, y, ridge=1e-9):
    k = len(rows[0])
    a = [[sum(r[i] * r[j] for r in rows) + (ridge if i == j else 0.0) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * yy for r, yy in zip(rows, y)) for i in range(k)]
    return solve(a, b)


def cholesky(k):
    n = len(k)
    jitter = 0.0
    while True:
        try:
            low = [[0.0] * n for _ in range(n)]
            for i in range(n):
                for j in range(i + 1):
                    s = k[i][j] + (jitter if i == j else 0.0) - sum(low[i][t] * low[j][t] for t in range(j))
                    if i == j:
                        if s <= 0:
                            raise ValueError
                        low[i][i] = math.sqrt(s)
                    else:
                        low[i][j] = s / low[j][j]
            return low
        except ValueError:
            jitter = jitter * 10 if jitter else 1e-8


def forward(low, b):
    y = []
    for i in range(len(b)):
        y.append((b[i] - sum(low[i][j] * y[j] for j in range(i))) / low[i][i])
    return y


def backward(low, y):
    n = len(y)
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(low[j][i] * x[j] for j in range(i + 1, n))) / low[i][i]
    return x


def gp_posterior(xs, ys, grid, noise, sf2, ell):
    n, m = len(xs), mean(ys)
    nz = noise if isinstance(noise, list) else [noise] * n
    k = [[sf2 * math.exp(-(xs[i] - xs[j]) ** 2 / (2 * ell * ell)) + (nz[i] if i == j else 0.0)
          for j in range(n)] for i in range(n)]
    low = cholesky(k)
    alpha = backward(low, forward(low, [y - m for y in ys]))
    mu, sd = [], []
    for g in grid:
        ks = [sf2 * math.exp(-(g - x) ** 2 / (2 * ell * ell)) for x in xs]
        mu.append(m + sum(a * b for a, b in zip(ks, alpha)))
        v = forward(low, ks)
        sd.append(math.sqrt(max(sf2 - sum(t * t for t in v), 1e-12)))
    return mu, sd


# ------------------------------------------------------------------- estimacion
class Trial:
    def __init__(self, i, cm, flick, track, over):
        self.i, self.cm, self.flick, self.track, self.over = i, cm, flick, track, over


def objective(trials, focus):
    wf, wt = {"flick": (1.0, 0.0), "track": (0.0, 1.0)}.get(focus, (0.5, 0.5))
    fs = [t.flick for t in trials if t.flick is not None]
    ts = [t.track for t in trials if t.track is not None]
    fm = mean(fs) if fs and mean(fs) > 0 else 1.0
    tm = mean(ts) if ts and mean(ts) > 0 else 1.0
    out = []
    for t in trials:
        y = 0.0
        if wf and t.flick is not None:
            y += wf * t.flick / fm
        if wt and t.track is not None:
            y += wt * t.track / tm
        out.append(y)
    return out


def detrend(xs, ys, idx):
    """Quita el efecto lineal de aprendizaje/fatiga estimado junto a una parabola.
    Devuelve (ys ajustados, varianza residual, residuos, deriva por prueba)."""
    n = len(ys)
    if n < 6 or len({round(x, 4) for x in xs}) < 3:
        return ys[:], max(variance(ys) * 0.4, 1e-4), [0.0] * n, 0.0
    mi = mean(idx)
    rows = [[x * x, x, 1.0, i - mi] for x, i in zip(xs, idx)]
    coef = lstsq(rows, ys)
    d = max(-0.05, min(0.05, coef[3]))
    adj = [y - d * (i - mi) for y, i in zip(ys, idx)]
    res = [y - sum(c * r for c, r in zip(coef, row)) for y, row in zip(ys, rows)]
    return adj, max(sum(r * r for r in res) / max(1, n - 4), 1e-4), res, coef[3]


def estimate(trials, focus, lo, hi, prior=None):
    span = math.log(hi / lo)
    xs = [(math.log(t.cm / lo)) / span for t in trials]
    ys, noise, res, drift = detrend(xs, objective(trials, focus), [t.i for t in trials])
    sf2 = max(variance(ys), noise * 0.5, 1e-3)
    grid = [i / (GRID_N - 1) for i in range(GRID_N)]
    gx, gy, gn = xs[:], ys[:], [noise] * len(xs)
    if ROBUST and len(res) >= 6:  # robusto: un bloque atipico (distraccion, fallo tonto) pesa menos
        sig = max(1.4826 * sorted(abs(r) for r in res)[len(res) // 2], math.sqrt(noise) * 0.5, 1e-3)
        gn = [noise * max(1.0, (abs(r) / (2.5 * sig)) ** 2) for r in res]
    for cm, y in (prior or [])[-8:]:  # observaciones de sesiones anteriores (menos fiables)
        t = math.log(cm / lo) / span
        if -0.02 <= t <= 1.02:
            gx.append(min(max(t, 0.0), 1.0))
            gy.append(y)
            gn.append(noise * PRIOR_NOISE_MULT)
    mu, sd = gp_posterior(gx, gy, grid, gn, sf2, 0.28)
    top = max(range(GRID_N), key=lambda i: mu[i])
    thr = mu[top] - ZONE * abs(mu[top])
    a = b = top
    while a > 0 and mu[a - 1] >= thr:
        a -= 1
    while b < GRID_N - 1 and mu[b + 1] >= thr:
        b += 1
    best_t = (a + b) / 2 / (GRID_N - 1)  # centro de la zona optima contigua
    cm_of = lambda t: lo * math.exp(t * span)
    return {"best": cm_of(best_t), "best_t": best_t, "zone": (cm_of(a / (GRID_N - 1)), cm_of(b / (GRID_N - 1))),
            "grid_cm": [cm_of(g) for g in grid], "mu": mu, "sd": sd, "xs": xs, "ys": ys,
            "noise": noise, "lo": lo, "hi": hi, "top_t": top / (GRID_N - 1), "drift": drift,
            "outliers": sum(1 for r, n_ in zip(res, gn) if n_ > noise * 1.0001)}


def percentile(v, p):
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))]


def bootstrap(trials, focus, lo, hi, final_cm, boots, rng, prior=None, current=None):
    n = len(trials)
    span = math.log(hi / lo)
    gi = round(math.log(final_cm / lo) / span * (GRID_N - 1))
    gi = min(max(gi, 0), GRID_N - 1)
    bests, regrets, gains = [], [], []
    ci_idx = None
    if current:
        ci_idx = round(math.log(current / lo) / span * (GRID_N - 1))
        ci_idx = ci_idx if 0 <= ci_idx < GRID_N else None
    for _ in range(boots):
        sub = [trials[rng.randrange(n)] for _ in range(n)]
        if len({round(t.cm, 3) for t in sub}) < 3:
            continue
        e = estimate(sub, focus, lo, hi, prior)
        mx = max(e["mu"])
        if ci_idx is not None and e["mu"][ci_idx]:
            gains.append(e["mu"][gi] / e["mu"][ci_idx] - 1)
        bests.append(e["best"])
        regrets.append(max(0.0, (mx - e["mu"][gi]) / abs(mx)) if mx else 0.0)
    if not bests:
        return ((final_cm, final_cm), 1.0, None) if current else ((final_cm, final_cm), 1.0)
    out = (percentile(bests, 0.05), percentile(bests, 0.95)), percentile(regrets, 0.9)
    if current:
        cmp = None
        if gains:
            cmp = {"cur": current, "gain": sum(gains) / len(gains),
                   "p_better": sum(1 for g in gains if g > 0) / len(gains)}
        return out + (cmp,)
    return out


def _phi(z):
    return math.exp(-z * z / 2) / math.sqrt(2 * math.pi)


def _cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


class Session:
    """Decide que sensibilidad probar y cuando parar."""

    def __init__(self, lo, hi, focus="mix", prior=None, seed=None, prior_obs=None, current=None, preset=1):
        if current:  # el rango siempre incluye tu sensibilidad actual
            lo, hi = min(lo, current / 1.15), max(hi, current * 1.15)
        self.lo, self.hi, self.focus = lo, hi, focus
        self.rng = random.Random(seed)
        self.prior_obs, self.current = prior_obs or [], current
        self.min_trials, self.max_trials, self.regret_stop = PRESETS.get(preset, PRESETS[1])
        self.trials, self.est, self.ci, self.regret = [], None, None, None
        self.ext = [0, 0]
        ts = INITIAL_T[:] if len(self.prior_obs) < 5 else [0.25, 0.75, 0.5]
        self.n_init = len(ts)
        span = math.log(hi / lo)
        self.queue = [lo * math.exp(t * span) for t in ts]
        if prior and lo <= prior <= hi:
            self.queue[-1] = prior
        if current:  # medir tambien tu sens actual para compararla con datos propios
            self.queue.append(current)
            self.n_init += 1
        self.rng.shuffle(self.queue)

    def next_cm(self):
        if self.queue:
            return self.queue.pop(0)
        e = self.est or estimate(self.trials, self.focus, self.lo, self.hi, self.prior_obs)
        best = max(e["mu"])
        recent = [t.cm for t in self.trials[-2:]]
        choice, score = None, -1.0
        for cm, m, s in zip(e["grid_cm"], e["mu"], e["sd"]):
            if any(abs(math.log(cm / r)) < 0.04 for r in recent):
                continue
            z = (m - best) / s
            ei = (m - best) * _cdf(z) + s * _phi(z)
            if ei > score:
                choice, score = cm, ei
        return choice or e["best"]

    def add(self, cm, flick, track, over):
        self.trials.append(Trial(len(self.trials), cm, flick, track, over))
        if len(self.trials) < self.n_init:
            return
        e = estimate(self.trials, self.focus, self.lo, self.hi, self.prior_obs)
        # si el optimo esta pegado a un borde, ampliamos el rango de busqueda
        if e["top_t"] > 0.92 and self.ext[1] < 3 and self.hi * 1.3 <= LIMIT_HI:
            self.hi *= 1.3
            self.ext[1] += 1
        elif e["top_t"] < 0.08 and self.ext[0] < 3 and self.lo / 1.3 >= LIMIT_LO:
            self.lo /= 1.3
            self.ext[0] += 1
        else:
            self.est = e
            self.ci, self.regret = bootstrap(self.trials, self.focus, self.lo, self.hi, e["best"], 50, self.rng,
                                             self.prior_obs)
            return
        self.est = estimate(self.trials, self.focus, self.lo, self.hi, self.prior_obs)
        self.ci, self.regret = None, None

    def done(self):
        n = len(self.trials)
        if n >= self.max_trials:
            return True
        return n >= self.min_trials and self.regret is not None and self.regret < self.regret_stop

    def confidence(self):
        if self.regret is None:
            return 0.0
        return max(0.0, min(1.0, 1 - self.regret / 0.05))

    def final(self):
        e = estimate(self.trials, self.focus, self.lo, self.hi, self.prior_obs)
        res = bootstrap(self.trials, self.focus, self.lo, self.hi, e["best"], 200, self.rng,
                        self.prior_obs, self.current)
        ci, regret = res[0], res[1]
        out = {"est": e, "ci": ci, "regret": regret, "best": e["best"], "zone": e["zone"],
               "vs_current": res[2] if self.current else None}
        if self.focus == "mix":
            for k in ("flick", "track"):
                out[k] = estimate(self.trials, k, self.lo, self.hi, self.prior_obs)["best"]
        pts = [(math.log(t.cm), t.over) for t in self.trials if t.over is not None]
        out["over_at_best"] = None
        if len(pts) >= 4 and len({round(p[0], 3) for p in pts}) >= 3:
            c = lstsq([[1.0, p[0]] for p in pts], [p[1] for p in pts])
            out["over_at_best"] = max(0.0, c[0] + c[1] * math.log(e["best"]))
            out["over_slope"] = c[1]
        return out


def best_ratio(results):
    """results: {ratio: [scores]} -> (ratio recomendado, mejora % frente a 1.0)."""
    means = {r: mean(v) for r, v in results.items() if v}
    if 1.0 not in means or len(means) < 4:
        return 1.0, 0.0
    xs = [math.log(r) for r in means]
    c = lstsq([[x * x, x, 1.0] for x in xs], list(means.values()))
    lo, hi = min(xs), max(xs)
    if c[0] < 0:
        vx = min(max(-c[1] / (2 * c[0]), lo), hi)
    else:
        vx = max(range(len(xs)), key=lambda i: list(means.values())[i])
        vx = xs[vx]
    pred = lambda x: c[0] * x * x + c[1] * x + c[2]
    gain = (pred(vx) - means[1.0]) / means[1.0] if means[1.0] else 0.0
    if gain < 0.03:
        return 1.0, max(gain, 0.0)
    return round(math.exp(vx), 2), gain
