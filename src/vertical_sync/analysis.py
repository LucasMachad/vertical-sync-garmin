"""Assessment logic: identify strengths and weaknesses from activity data."""

from .config import get_plan_week


def assess_activity(metrics: dict) -> list[dict]:
    """Assess a single activity. Returns list of observations."""
    obs = []
    # Climbing rate, cadence and vertical-ratio thresholds below are calibrated
    # for running biomechanics and don't transfer to cycling — skip them on the bike.
    is_cycling = metrics.get("sport") == "cycling"

    # --- HR zone discipline ---
    zones = metrics.get("hr_zones", {})
    if zones:
        z1_z2 = zones.get("Z1", {}).get("pct", 0) + zones.get("Z2", {}).get("pct", 0)
        z4_z5 = zones.get("Z4", {}).get("pct", 0) + zones.get("Z5", {}).get("pct", 0)
        z3 = zones.get("Z3", {}).get("pct", 0)

        # Long run (>1h) zone analysis
        if metrics["duration_s"] > 3600:
            if z1_z2 >= 75:
                obs.append({
                    "type": "strength",
                    "category": "zone_discipline",
                    "detail": f"{z1_z2:.0f}% du temps en Z1-Z2 sur une sortie longue ({metrics['duration']})",
                    "implication": "Bonne gestion de l'intensite en endurance fondamentale",
                })
            elif z1_z2 < 50:
                obs.append({
                    "type": "weakness",
                    "category": "zone_discipline",
                    "detail": f"Seulement {z1_z2:.0f}% en Z1-Z2 sur une sortie longue ({metrics['duration']})",
                    "implication": "Risque de fatigue accumulee — respecter les zones basses en endurance",
                })

        # Grey zone alert (too much Z3 without clear quality intent)
        if z3 > 40:
            obs.append({
                "type": "weakness",
                "category": "zone_discipline",
                "detail": f"{z3:.0f}% du temps en Z3 (zone grise)",
                "implication": "Ni assez facile pour recuperer, ni assez dur pour progresser. Polariser davantage.",
            })

        # High intensity session detection
        if z4_z5 > 25 and metrics["duration_s"] > 1800:
            obs.append({
                "type": "info",
                "category": "intensity",
                "detail": f"{z4_z5:.0f}% en Z4-Z5 — seance haute intensite",
                "implication": "Prevoir 48h de recuperation avant la prochaine seance dure",
            })

    # --- Cardiac drift ---
    drift = metrics.get("cardiac_drift_pct")
    if drift is not None:
        if abs(drift) < 3:
            obs.append({
                "type": "strength",
                "category": "aerobic_fitness",
                "detail": f"Derive cardiaque tres faible ({drift:+.1f}%)",
                "implication": "Excellente stabilite cardiaque, bonne base aerobie",
            })
        elif drift > 8:
            obs.append({
                "type": "weakness",
                "category": "aerobic_fitness",
                "detail": f"Derive cardiaque elevee ({drift:+.1f}%)",
                "implication": "Fatigue, deshydratation possible ou base aerobie a renforcer",
            })
        elif drift > 5:
            obs.append({
                "type": "info",
                "category": "aerobic_fitness",
                "detail": f"Derive cardiaque moderee ({drift:+.1f}%)",
                "implication": "Normal sur effort long, surveiller hydratation et alimentation",
            })

    # --- Climbing efficiency (running-specific ascent-rate thresholds) ---
    ascent = metrics.get("ascent_m", 0)
    if not is_cycling and ascent > 100:
        rate = metrics.get("ascent_rate_m_h", 0)
        if rate >= 600:
            obs.append({
                "type": "strength",
                "category": "climbing",
                "detail": f"Vitesse ascensionnelle {rate}m/h (D+ {ascent}m)",
                "implication": "Bon rythme en montee pour le profil course (3200m D+ sur 52km)",
            })
        elif rate < 400:
            obs.append({
                "type": "weakness",
                "category": "climbing",
                "detail": f"Vitesse ascensionnelle {rate}m/h (D+ {ascent}m)",
                "implication": "Travailler la marche active en montee (>20% pente), viser >500m/h",
            })

    # --- Cadence (spm thresholds — meaningless for cycling rpm) ---
    cadence = metrics.get("avg_cadence", 0)
    if not is_cycling and cadence and cadence > 0:
        if 170 <= cadence <= 185:
            obs.append({
                "type": "strength",
                "category": "technique",
                "detail": f"Cadence optimale ({cadence} spm)",
                "implication": "Bonne frequence de pas, efficacite biomecanique",
            })
        elif cadence < 155:
            obs.append({
                "type": "weakness",
                "category": "technique",
                "detail": f"Cadence basse ({cadence} spm)",
                "implication": "Viser 170+ spm pour reduire l'impact et ameliorer l'efficacite en trail",
            })

    # --- Vertical ratio (terrain context, running-specific) ---
    vr = metrics.get("vertical_ratio_m_km", 0)
    if not is_cycling and vr > 80:
        obs.append({
            "type": "info",
            "category": "terrain",
            "detail": f"Ratio vertical eleve ({vr}m/km) — terrain tres montagneux",
            "implication": "Entrainement specifique pour la course (ratio course ~61m/km)",
        })

    return obs


def assess_week(week_summary: dict, activities: list[dict], start_date: int) -> list[dict]:
    """Assess weekly training vs plan targets. Returns observations."""
    obs = []
    plan = get_plan_week(start_date)

    if plan:
        obs.append({
            "type": "info",
            "category": "plan",
            "detail": f"Semaine {plan['week']} — Phase: {plan['phase']}",
            "implication": "",
        })

        # Volume hours
        actual_h = week_summary.get("total_time_h", 0)
        target_h = plan["target_hours"]
        if target_h > 0:
            diff_pct = (actual_h - target_h) / target_h * 100
            if abs(diff_pct) <= 10:
                obs.append({
                    "type": "strength",
                    "category": "volume",
                    "detail": f"Volume horaire conforme: {actual_h:.1f}h vs {target_h:.1f}h cible ({diff_pct:+.0f}%)",
                    "implication": "Bonne adherence au programme",
                })
            elif diff_pct < -15:
                obs.append({
                    "type": "weakness",
                    "category": "volume",
                    "detail": f"Volume insuffisant: {actual_h:.1f}h vs {target_h:.1f}h cible ({diff_pct:+.0f}%)",
                    "implication": "Compenser si possible la semaine suivante ou ajuster les objectifs",
                })
            elif diff_pct > 15:
                obs.append({
                    "type": "weakness",
                    "category": "volume",
                    "detail": f"Volume excessif: {actual_h:.1f}h vs {target_h:.1f}h cible ({diff_pct:+.0f}%)",
                    "implication": "Surveiller la fatigue et la recuperation",
                })

        # D+ target
        actual_dp = week_summary.get("total_dplus", 0)
        target_dp = plan["target_dplus"]
        if target_dp > 0:
            diff_pct = (actual_dp - target_dp) / target_dp * 100
            if abs(diff_pct) <= 15:
                obs.append({
                    "type": "strength",
                    "category": "vertical",
                    "detail": f"D+ conforme: {actual_dp}m vs {target_dp}m cible ({diff_pct:+.0f}%)",
                    "implication": "Travail vertical en ligne avec les objectifs",
                })
            elif diff_pct < -20:
                obs.append({
                    "type": "weakness",
                    "category": "vertical",
                    "detail": f"D+ insuffisant: {actual_dp}m vs {target_dp}m cible ({diff_pct:+.0f}%)",
                    "implication": "Choisir des parcours plus vallonnes ou ajouter du denivele",
                })
            elif diff_pct > 20:
                obs.append({
                    "type": "info",
                    "category": "vertical",
                    "detail": f"D+ au-dessus de la cible: {actual_dp}m vs {target_dp}m ({diff_pct:+.0f}%)",
                    "implication": "OK si la recuperation suit, sinon alleger la semaine suivante",
                })

        # Sessions count
        actual_s = week_summary.get("runs", 0)
        target_s = plan["target_sessions"]
        if actual_s < target_s:
            obs.append({
                "type": "weakness",
                "category": "frequency",
                "detail": f"{actual_s} seance(s) vs {target_s} prevue(s)",
                "implication": "Seance(s) manquante(s) — evaluer si c'est recuperation choisie ou contrainte",
            })
        elif actual_s >= target_s:
            obs.append({
                "type": "strength",
                "category": "frequency",
                "detail": f"{actual_s} seance(s) realisee(s) sur {target_s} prevue(s)",
                "implication": "Bonne regularite d'entrainement",
            })

    # --- Overall intensity polarization ---
    all_z12 = []
    all_z45 = []
    for a in activities:
        zones = a.get("hr_zones", {})
        if zones:
            z12 = zones.get("Z1", {}).get("pct", 0) + zones.get("Z2", {}).get("pct", 0)
            z45 = zones.get("Z4", {}).get("pct", 0) + zones.get("Z5", {}).get("pct", 0)
            all_z12.append(z12)
            all_z45.append(z45)

    if all_z12:
        avg_easy = sum(all_z12) / len(all_z12)
        avg_hard = sum(all_z45) / len(all_z45)
        if avg_easy >= 70:
            obs.append({
                "type": "strength",
                "category": "polarization",
                "detail": f"Distribution polarisee: {avg_easy:.0f}% en zones faciles en moyenne",
                "implication": "Bon respect du modele 80/20",
            })
        elif avg_easy < 50:
            obs.append({
                "type": "weakness",
                "category": "polarization",
                "detail": f"Seulement {avg_easy:.0f}% en zones faciles — entrainement trop intense",
                "implication": "Risque de zone grise, ralentir les sorties faciles",
            })

    # --- Average cardiac drift across runs ---
    drifts = [a["cardiac_drift_pct"] for a in activities if a.get("cardiac_drift_pct") is not None]
    if drifts:
        avg_drift = sum(drifts) / len(drifts)
        if avg_drift > 7:
            obs.append({
                "type": "weakness",
                "category": "fatigue",
                "detail": f"Derive cardiaque moyenne elevee sur la semaine ({avg_drift:.1f}%)",
                "implication": "Signes de fatigue accumulee — envisager plus de repos",
            })

    return obs


def assess_session(session: dict) -> list[dict]:
    """Assess the execution of a quality session (output of analyze_session).

    Flags only what the numbers show — target compliance, start too fast,
    fade within a set, regularity, rising HR cost. Whether a fade was planned
    (progressive set) or a miss was terrain is for the LLM to judge."""
    obs = []

    # --- Target compliance ---
    s = session["summary"]
    if s["with_target"]:
        ratio = s["in_target"] / s["with_target"]
        if ratio >= 0.8:
            obs.append({
                "type": "strength",
                "category": "target",
                "detail": f"{s['in_target']}/{s['with_target']} reps dans la cible",
                "implication": "Seance executee comme prescrite",
            })
        elif ratio < 0.6:
            obs.append({
                "type": "weakness",
                "category": "target",
                "detail": f"Seulement {s['in_target']}/{s['with_target']} reps dans la cible",
                "implication": "Cible mal calibree, ou execution a revoir (terrain, fatigue, depart)",
            })

    # --- Start too fast: first reps of a set well ahead of target ---
    for st in session["sets"]:
        reps = [r for r in session["reps"] if r["set"] == st["set"]]
        early = [r["target_delta_s_per_km"] for r in reps[:2] if r["target_delta_s_per_km"] is not None]
        if early and max(early) <= -5:
            obs.append({
                "type": "weakness",
                "category": "pacing",
                "detail": f"Serie {st['set']} : premieres reps {-max(early)}+ s/km plus rapides que la cible",
                "implication": "Depart trop rapide — paye en fin de serie ou en recup",
            })

    # --- Per-set execution (sets of >= 3 reps) ---
    for st in session["sets"]:
        if st["reps"] < 3:
            continue
        fade = st["fade_pct"]
        if fade is not None and fade <= -3:
            obs.append({
                "type": "weakness",
                "category": "fade",
                "detail": f"Serie {st['set']} : allure en baisse de {-fade:.1f}% entre debut et fin",
                "implication": "Intensite ou volume au-dessus de ce qui est tenable ce jour-la",
            })
        elif fade is not None and fade >= 2:
            obs.append({
                "type": "info",
                "category": "fade",
                "detail": f"Serie {st['set']} : finit {fade:.1f}% plus vite qu'au debut",
                "implication": "Progressif, ou debut de serie trop prudent",
            })
        cv = st["pace_cv_pct"]
        if cv is not None and cv <= 2:
            obs.append({
                "type": "strength",
                "category": "regularity",
                "detail": f"Serie {st['set']} : reps tres regulieres (CV allure {cv}%)",
                "implication": "Bonne maitrise de l'allure",
            })
        elif cv is not None and cv >= 6:
            obs.append({
                "type": "weakness",
                "category": "regularity",
                "detail": f"Serie {st['set']} : reps irregulieres (CV allure {cv}%)",
                "implication": "Allure mal tenue (ou terrain variable — verifier vert_pct)",
            })
        # On short reps (30/30, 60/60) HR is still climbing over the first reps,
        # so a rising end-of-rep HR is lag, not cost — only flag long reps.
        drift = st["end_hr_drift_bpm"]
        long_reps = st["work_time_s"] / st["reps"] >= 120
        if long_reps and drift is not None and drift >= 8 and (fade is None or fade > -3):
            obs.append({
                "type": "info",
                "category": "hr_cost",
                "detail": f"Serie {st['set']} : FC de fin de rep +{drift} bpm a allure tenue",
                "implication": "Cout cardiaque qui monte — fatigue ou chaleur",
            })

    # --- HR recovery between long reps ---
    first, last = s["hr_drop_60s_first"], s["hr_drop_60s_last"]
    if first is not None and last is not None and first - last >= 8:
        obs.append({
            "type": "info",
            "category": "recovery",
            "detail": f"Baisse de FC en 60 s de recup : {first} -> {last} bpm",
            "implication": "Recuperation qui se degrade au fil de la seance",
        })

    return obs


def assess_cycle(cycle: dict) -> list[dict]:
    """Assess a training cycle (output of compute_cycle): intensity
    distribution, trail maintenance and the easy-run EF trend. Weeks with
    fewer than 3 runs (recovery, travel) are skipped for the distribution."""
    obs = []
    weeks = [w for w in cycle["weeks"] if w["runs"] >= 3]

    # --- Intensity distribution: easy runs drifting into Z3 ---
    grey = [w["week_start"] for w in weeks if w["z3_pct"] > 20]
    if grey:
        obs.append({
            "type": "weakness",
            "category": "polarization",
            "detail": f"Z3 > 20% du temps sur {len(grey)} semaine(s) : {', '.join(grey)}",
            "implication": "Footings qui glissent en zone grise — piege classique d'un bloc vitesse",
        })
    if weeks:
        easy = sum(w["z12_pct"] for w in weeks) / len(weeks)
        obs.append({
            "type": "strength" if easy >= 75 else "info",
            "category": "polarization",
            "detail": f"{easy:.0f}% du temps en Z1-Z2 en moyenne sur {len(weeks)} semaine(s)",
            "implication": "Repere 80/20 : la qualite ne paie que si le reste est vraiment facile",
        })

    # --- Trail maintenance: a long run every week ---
    no_long = [w["week_start"] for w in weeks
               if not w["long_run"] or w["long_run"]["duration_s"] < 90 * 60]
    if no_long:
        obs.append({
            "type": "info",
            "category": "trail_maintenance",
            "detail": f"Pas de sortie >= 1h30 sur {len(no_long)} semaine(s) : {', '.join(no_long)}",
            "implication": "Maintien trail : 1 longue vallonnee par semaine prevue au cycle",
        })

    # --- Easy-run EF trend (first vs last two measured weeks) ---
    efs = [w["easy_ef"] for w in cycle["weeks"] if w["easy_ef"] is not None]
    if len(efs) >= 4:
        first, last = sum(efs[:2]) / 2, sum(efs[-2:]) / 2
        change = (last - first) / first * 100
        obs.append({
            "type": "strength" if change >= 2 else "weakness" if change <= -2 else "info",
            "category": "efficiency",
            "detail": f"EF footings {first:.3f} -> {last:.3f} ({change:+.1f}%)",
            "implication": "Tendance d'economie aerobie (temperature non corrigee — voir vs efficiency)",
        })

    return obs
