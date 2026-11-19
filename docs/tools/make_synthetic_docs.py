# make_synthetic_docs.py: writes synthetic patient notes in the dashboard's upload format.
#   python docs/tools/make_synthetic_docs.py                         # P008-P107 (100 files)
#   python docs/tools/make_synthetic_docs.py --start 108 --count 100  # P108-P207
# Files go to docs/samples/generated/ wherever you run it from. Same --start = same files (seeded).
import argparse
import random
from datetime import date, timedelta
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--start", type=int, default=8, help="first patient number (8 -> P008)")
ap.add_argument("--count", type=int, default=100, help="how many documents")
ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "samples" / "generated"))
args = ap.parse_args()
if args.start + args.count - 1 > 999:
    raise SystemExit("IDs are P### (max P999): lower --start or --count")

# seed 7 for the default run keeps your existing P008-P107 files identical if you re-run it;
# any other start gets its own seed, so a new batch has different patients
random.seed(7 if args.start == 8 else args.start)
OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)

FIRST = ["Ava", "Ben", "Carla", "Dev", "Elena", "Femi", "Grace", "Hugo", "Iris", "Jonah",
         "Kira", "Luis", "Mina", "Noah", "Olga", "Pavel", "Quinn", "Rosa", "Sami", "Tara",
         "Uma", "Victor", "Wen", "Xavier", "Yara", "Zane"]
LAST = ["Abbott", "Banerjee", "Costa", "Duarte", "Eriksen", "Fischer", "Garcia", "Haddad",
        "Ibarra", "Jensen", "Kowalski", "Lindgren", "Moreau", "Nakamura", "Osei", "Petrov",
        "Quint", "Rossi", "Silva", "Tanaka", "Ueda", "Varga", "Walsh", "Young", "Zhou"]
DIAGNOSES = ["Oropharyngeal SCC", "Laryngeal SCC", "Oral cavity SCC", "Tongue SCC (oral cavity)",
             "Hypopharyngeal SCC", "Nasopharyngeal carcinoma"]

def sev(): return random.randint(1, 9)

SYMPTOM_LINES = [
    lambda: f"Nausea about {sev()}/10 today.",
    lambda: f"Nausea around {sev()}/10 after lunch, felt queasy most of the day.",
    lambda: f"Threw up {random.choice(['once', 'twice', 'three times'])} after dinner.",
    lambda: f"Vomited {random.choice(['once', 'twice'])} yesterday.",
    lambda: "No vomiting today.",
    lambda: f"Mouth sores {random.choice(['mild', 'moderate', 'severe'])}.",
    lambda: f"Mouth sores maybe {sev()} out of 10, hard to eat solid food.",
    lambda: f"Throat pain around {sev()}/10.",
    lambda: f"Pain about {sev()}/10 when eating.",
    lambda: "Swallowing is getting harder, only managing soft food.",
    lambda: "Choked on water once.",
    lambda: "Really tired, slept most of the afternoon.",
    lambda: "Felt off all day, not sure why.",
    lambda: "No fever.",
]

for i in range(args.start, args.start + args.count):
    pid = f"P{i:03d}"
    name = f"{random.choice(FIRST)} {random.choice(LAST)}"
    dob = date(random.randint(1950, 1985), random.randint(1, 12), random.randint(1, 28))
    dx_date = date(2026, 7, 15) + timedelta(days=random.randint(0, 40))
    tx_start = dx_date + timedelta(days=random.randint(21, 35))
    report_date = tx_start + timedelta(days=random.randint(7, 30))
    antiemetic = random.choice(["ondansetron 8 mg", "ondansetron 8 mg, olanzapine 5 mg"])
    lines = [f() for f in random.sample(SYMPTOM_LINES, k=random.randint(3, 6))]

    text = "\n".join([
        f"Patient: {pid} ({name}) - synthetic test report",
        f"DOB: {dob}",
        f"Diagnosis: {random.choice(DIAGNOSES)}, diagnosed {dx_date}",
        f"Treatment start: {tx_start} (radiation + weekly cisplatin)",
        f"Medications: cisplatin 40 mg/m2 weekly, {antiemetic}",
        "",
        f"Date: {report_date}",
        *lines,
    ]) + "\n"
    (OUT / f"{pid}_{name.replace(' ', '_')}_report.txt").write_text(text)

print(f"Wrote {args.count} files to {OUT}/ (P{args.start:03d}-P{args.start + args.count - 1:03d})")
