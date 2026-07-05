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
