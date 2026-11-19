# Synthetic_datagenerator.py — fictional oncology (head & neck chemoradiation) patients.
# No real patient data. Seeded, so every run produces the same dataset.
#
# Timeline per patient:
#   biopsy/diagnosis -> first consult (1-2 wk later) -> staging PET/CT, dental review, audiology
#   -> treatment review + radiation planning (week before start) -> chemoradiation starts Sep 7
#   -> weekly on-treatment visits (days 7, 14, 21, 28) -> daily symptom reports throughout.
import random
import pandas as pd
from datetime import date, timedelta
from schema import empty_store, save

random.seed(42)                 # drives reports + weights (unchanged from earlier versions)
hist = random.Random(7)         # separate stream for history, so adding it didn't change the reports

START = date(2026, 9, 7)
WEEKS = 5
SWITCH_DAY = 21                 # worsening patients switch ondansetron -> olanzapine on this day

MED_ONC = "Dr. M. Chen (Medical Oncology)"
RAD_ONC = "Dr. R. Patel (Radiation Oncology)"
NP = "J. Alvarez, NP (Oncology)"

PATIENTS = [
    ("P001", "Alex Rivera",   "1968-03-12", "Oropharyngeal SCC"),
    ("P002", "Dana Okafor",   "1975-11-02", "Laryngeal SCC"),
    ("P003", "Sam Lindqvist", "1961-07-25", "Oral cavity SCC"),
    ("P004", "Priya Nair",    "1979-01-18", "Nasopharyngeal carcinoma"),
    ("P005", "Jordan Blake",  "1970-05-30", "Oropharyngeal SCC"),
]
WORSENING = ("P001", "P004")

# (template text, symptoms the text mentions — incl. denied ones). The label list is the
# "silver" ground truth used by evaluate.py to measure symptom recall over every report.
TEMPLATES = [
    ("Threw up {n} times after dinner, felt nauseous all morning.", ["vomiting", "nausea"]),
    ("Nausea about {s}/10 today, kept food down though.",           ["nausea"]),
    ("Mouth sores are worse, maybe {s} out of 10. Hard to eat anything solid.", ["mouth_sores"]),
    ("Pain in my throat around {s}/10, took the lidocaine rinse twice.",       ["pain"]),
    ("Swallowing is getting harder, choked on water once.",          ["swallowing_difficulty"]),
    ("Really wiped out today, slept most of the afternoon.",         ["fatigue"]),
    ("No vomiting today. Nausea mild.",                              ["vomiting", "nausea"]),   # negation
    ("Felt off all day, not sure why.",                              ["other"]),                # vague
    ("Vomited once last night after the chemo infusion.",            ["vomiting"]),  # relative time + med link
    ("Throat pain is severe today, could barely talk.",              ["pain"]),      # severity word, no number
    ("No mouth sores, but pain about {s}/10 when I swallow.",        ["mouth_sores", "pain", "swallowing_difficulty"]),
]
# Only makes sense after the antiemetic switch, so only offered to switched patients from SWITCH_DAY.
NEW_PILL = ("Nausea was bad after the new pill, maybe {s}/10.", ["nausea"])

# Deliberate same-day contradictions (morning vs evening) to exercise conflict detection.
CONFLICTS = {
    ("P001", 24): [("08", "Nausea about 8/10 this morning, couldn't eat breakfast.", ["nausea"]),
                   ("21", "No nausea today, felt fine all day.",                      ["nausea"])],
    ("P004", 27): [("08", "Nausea about 7/10, stayed in bed.",                        ["nausea"]),
                   ("21", "Nausea about 2/10 today, pretty good day.",               ["nausea"])],
}


def make_text(day_idx, worsening):
    pool = TEMPLATES + ([NEW_PILL] if worsening and day_idx >= SWITCH_DAY else [])
    text, labels = random.choice(pool)
    base = 3 + (day_idx // 7) * (1.2 if worsening else 0.4)
    s = max(0, min(10, int(random.gauss(base, 1.5))))
    return text.format(s=s, n=random.choice([1, 2, 3])), labels


def d(day_offset):
    """Date string relative to treatment start."""
    return str(START + timedelta(days=day_offset))


def make_history(pid, dx, worsening):
    """Diagnosis, visits, procedures and medications for one patient, all before/around START."""
    diag = -hist.randint(42, 56)                      # biopsy 6-8 weeks before treatment
    first = diag + hist.randint(7, 12)                # first consult 1-2 weeks after biopsy
    planning = -hist.randint(7, 10)                   # radiation simulation the week before start
    site = dx.split()[0].lower()

    procs = [
        ("Biopsy (" + site + ")",        diag,                         "completed"),
        ("PET/CT staging",               first + hist.randint(3, 6),   "completed"),
        ("Dental review pre-radiation",  first + hist.randint(7, 10),  "completed"),
        ("Audiology baseline (cisplatin)", first + hist.randint(8, 12), "completed"),
        ("Radiation planning CT (simulation)", planning,               "completed"),
    ]
    if pid == "P001":
        procs.append(("Feeding tube (PEG) placement", 26, "completed"))
    elif pid == "P004":
        procs.append(("Feeding tube (PEG) placement", 37, "scheduled"))
    procedures = [[f"PR-{pid}-{i:02d}", pid, name, d(day), status]
                  for i, (name, day, status) in enumerate(sorted(procs, key=lambda p: p[1]), 1)]

    v = [(first, "first consult", MED_ONC,
          f"New patient, {dx} confirmed on biopsy. Staging PET/CT, dental and audiology ordered. "
          f"Plan: concurrent cisplatin + radiation."),
         (-3, "treatment review", RAD_ONC,
          "Staging complete. Consent for chemoradiation signed. Start date confirmed for Sep 7.")]
    for day in (7, 14, 21, 28):
        if worsening and day == 21:
            note = ("Nausea uncontrolled on ondansetron, weight trending down. "
                    "Switched antiemetic to olanzapine 5 mg nightly. Dietitian referral.")
        elif worsening and day == 28:
            note = ("Weight loss >5% since start. " +
                    ("PEG placed Oct 3, tolerating feeds." if pid == "P001"
                     else "PEG placement scheduled for Oct 14."))
        elif day == 14:
            note = "Mucositis starting, grade 1-2. Started lidocaine viscous rinse for mouth pain."
        else:
            note = "On-treatment check. Tolerating chemoradiation, symptoms as expected."
        vtype = "treatment review" if day in (14, 28) else "follow-up"
        v.append((day, vtype, MED_ONC if vtype == "treatment review" else NP, note))
    visits = [[f"V-{pid}-{i:02d}", pid, d(day), t, prov, note]
              for i, (day, t, prov, note) in enumerate(sorted(v, key=lambda x: x[0]), 1)]

    meds = [[pid, "cisplatin", "100 mg/m2 every 3 weeks", d(0), None, MED_ONC, "concurrent chemoradiation"],
            [pid, "lidocaine viscous 2%", "15 mL swish/spit as needed", d(14), None, MED_ONC,
             "mouth pain from mucositis"]]
    if worsening:
        meds += [[pid, "ondansetron", "8 mg every 8 h as needed", d(0), d(SWITCH_DAY - 1), MED_ONC,
                  "chemotherapy-induced nausea prophylaxis"],
                 [pid, "olanzapine", "5 mg nightly", d(SWITCH_DAY), None, NP,
                  "breakthrough nausea despite ondansetron"]]
    else:
        meds.append([pid, "ondansetron", "8 mg every 8 h as needed", d(0), None, MED_ONC,
                     "chemotherapy-induced nausea prophylaxis"])

    return d(diag), d(first), visits, procedures, meds


def main(folder="data"):
    global hist
    random.seed(42)                 # re-seed so repeated calls in one process give identical data
    hist = random.Random(7)
    store = empty_store()
    patients, visits, procedures, meds, reports, vitals, silver = [], [], [], [], [], [], []
    report_id = 0

    def add_report(pid, day, hour, text, labels):
        nonlocal report_id
        report_id += 1
        reports.append([report_id, pid, f"{day}T{hour}:{random.randint(0, 59):02d}:00", "free_text", text])
        silver.extend({"report_id": report_id, "symptom": s} for s in labels)

    for pid, name, dob, dx in PATIENTS:
        worsening = pid in WORSENING
        diag_date, first_visit, p_visits, p_procs, p_meds = make_history(pid, dx, worsening)
        patients.append([pid, name, dob, dx, diag_date, first_visit, str(START)])
        visits += p_visits
        procedures += p_procs
        meds += p_meds

        weight = random.uniform(62, 85)
        for day_idx in range(WEEKS * 7):
            day = START + timedelta(days=day_idx)
            if (pid, day_idx) in CONFLICTS:
                for hour, text, labels in CONFLICTS[(pid, day_idx)]:
                    add_report(pid, day, hour, text, labels)
            else:
                r = random.random()
                n_reports = 2 if r < 0.10 else (0 if r < 0.25 else 1)
                for _ in range(n_reports):
                    text, labels = make_text(day_idx, worsening)
                    add_report(pid, day, random.choice(["08", "13", "21"]), text, labels)
            # worsening patients lose ~7 kg over 5 weeks (crosses the 5% alert), stable ones ~2 kg
            weight -= random.uniform(0.05, 0.35) if worsening else random.uniform(0.02, 0.10)
            vitals.append([pid, str(day), "weight_kg", round(weight, 1)])

    for table, rows in [("patients", patients), ("visits", visits), ("procedures", procedures),
                        ("medications", meds), ("reports", reports), ("vitals", vitals)]:
        store[table] = pd.DataFrame(rows, columns=store[table].columns)
    save(store, folder)
    pd.DataFrame(silver).to_csv(f"{folder}/silver_labels.csv", index=False)
    print(f"{len(reports)} reports, {len(vitals)} weight rows, {len(visits)} visits, "
          f"{len(procedures)} procedures, {len(meds)} medication periods written to {folder}/")


if __name__ == "__main__":
    main()
