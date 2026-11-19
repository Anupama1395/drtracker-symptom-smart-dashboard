# Synthetic_datagenerator.py — fictional oncology (head & neck chemoradiation) patients.
# No real patient data. Seeded, so every run produces the same dataset.
import random
import pandas as pd
from datetime import date, timedelta
from schema import empty_store, save

random.seed(42)
START = date(2026, 9, 7)
WEEKS = 5
SWITCH_DAY = 21          # worsening patients switch ondansetron -> olanzapine on this day

PATIENTS = [
    ("P001", "Alex Rivera",   "1968-03-12", "Oropharyngeal SCC"),
    ("P002", "Dana Okafor",   "1975-11-02", "Laryngeal SCC"),
    ("P003", "Sam Lindqvist", "1961-07-25", "Oral cavity SCC"),
    ("P004", "Priya Nair",    "1979-01-18", "Nasopharyngeal carcinoma"),
    ("P005", "Jordan Blake",  "1970-05-30", "Oropharyngeal SCC"),
]

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


def main():
    store = empty_store()
    patients, meds, reports, vitals, silver = [], [], [], [], []
    report_id = 0

    def add_report(pid, day, hour, text, labels):
        nonlocal report_id
        report_id += 1
        reports.append([report_id, pid, f"{day}T{hour}:{random.randint(0, 59):02d}:00", "free_text", text])
        silver.extend({"report_id": report_id, "symptom": s} for s in labels)

    for pid, name, dob, dx in PATIENTS:
        worsening = pid in ("P001", "P004")
        patients.append([pid, name, dob, dx, str(START)])

        meds.append([pid, "cisplatin", "100 mg/m2 weekly", str(START), None])
        if worsening:
            meds.append([pid, "ondansetron", "8 mg", str(START), str(START + timedelta(days=SWITCH_DAY - 1))])
            meds.append([pid, "olanzapine", "5 mg", str(START + timedelta(days=SWITCH_DAY)), None])
        else:
            meds.append([pid, "ondansetron", "8 mg", str(START), None])

        weight = random.uniform(62, 85)
        for d in range(WEEKS * 7):
            day = START + timedelta(days=d)
            if (pid, d) in CONFLICTS:
                for hour, text, labels in CONFLICTS[(pid, d)]:
                    add_report(pid, day, hour, text, labels)
            else:
                r = random.random()
                n_reports = 2 if r < 0.10 else (0 if r < 0.25 else 1)
                for _ in range(n_reports):
                    text, labels = make_text(d, worsening)
                    add_report(pid, day, random.choice(["08", "13", "21"]), text, labels)
            weight -= random.uniform(0.05, 0.35 if worsening else 0.15)
            vitals.append([pid, str(day), "weight_kg", round(weight, 1)])

    store["patients"]    = pd.DataFrame(patients, columns=store["patients"].columns)
    store["medications"] = pd.DataFrame(meds,     columns=store["medications"].columns)
    store["reports"]     = pd.DataFrame(reports,  columns=store["reports"].columns)
    store["vitals"]      = pd.DataFrame(vitals,   columns=store["vitals"].columns)
    save(store)
    pd.DataFrame(silver).to_csv("data/silver_labels.csv", index=False)
    print(f"{len(reports)} reports, {len(vitals)} weight rows written to data/")


if __name__ == "__main__":
    main()
