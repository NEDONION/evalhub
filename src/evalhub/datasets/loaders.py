from collections.abc import Iterable
import csv
import json
from pathlib import Path
import re
import tarfile
from urllib.request import urlretrieve

from evalhub.datasets.catalog import DatasetSpec, get_dataset_spec
from evalhub.domain import EvaluationSample


MMLU_SUBJECTS = [
    "abstract_algebra",
    "anatomy",
    "astronomy",
    "business_ethics",
    "clinical_knowledge",
    "college_biology",
    "college_chemistry",
    "college_computer_science",
    "college_mathematics",
    "college_medicine",
    "college_physics",
    "computer_security",
    "conceptual_physics",
    "econometrics",
    "electrical_engineering",
    "elementary_mathematics",
    "formal_logic",
    "global_facts",
    "high_school_biology",
    "high_school_chemistry",
    "high_school_computer_science",
    "high_school_european_history",
    "high_school_geography",
    "high_school_government_and_politics",
    "high_school_macroeconomics",
    "high_school_mathematics",
    "high_school_microeconomics",
    "high_school_physics",
    "high_school_psychology",
    "high_school_statistics",
    "high_school_us_history",
    "high_school_world_history",
    "human_aging",
    "human_sexuality",
    "international_law",
    "jurisprudence",
    "logical_fallacies",
    "machine_learning",
    "management",
    "marketing",
    "medical_genetics",
    "miscellaneous",
    "moral_disputes",
    "moral_scenarios",
    "nutrition",
    "philosophy",
    "prehistory",
    "professional_accounting",
    "professional_law",
    "professional_medicine",
    "professional_psychology",
    "public_relations",
    "security_studies",
    "sociology",
    "us_foreign_policy",
    "virology",
    "world_religions",
]


def prepare_dataset(name: str, *, root: Path | str = ".") -> Path:
    spec = get_dataset_spec(name)
    root_path = Path(root)
    if name == "gsm8k":
        return _prepare_gsm8k(spec, root_path)
    if name == "mmlu":
        return _prepare_mmlu(spec, root_path)
    raise KeyError(f"unsupported dataset: {name}")


def load_samples(
    name: str,
    *,
    root: Path | str = ".",
    limit: int | None = None,
    subject: str | None = None,
) -> list[EvaluationSample]:
    root_path = Path(root)
    if name == "gsm8k":
        return _limited(_load_gsm8k(root_path), limit)
    if name == "mmlu":
        subjects = MMLU_SUBJECTS if subject in (None, "all") else [subject]
        return _limited(_load_mmlu(root_path, subjects), limit)
    raise KeyError(f"unsupported dataset: {name}")


def _prepare_gsm8k(spec: DatasetSpec, root: Path) -> Path:
    local_path = root / spec.local_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if not local_path.exists():
        urlretrieve(spec.source_url, local_path)
    return local_path


def _prepare_mmlu(spec: DatasetSpec, root: Path) -> Path:
    local_dir = root / spec.local_path
    if local_dir.exists() and any(local_dir.glob("*_test.csv")):
        return local_dir

    archive_path = root / "data/raw/mmlu/data.tar"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if not archive_path.exists():
        urlretrieve(spec.source_url, archive_path)

    with tarfile.open(archive_path) as archive:
        _safe_extract(archive, root / "data/raw/mmlu")
    return local_dir


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.getmembers():
        member_path = (destination / member.name).resolve()
        if not str(member_path).startswith(str(destination)):
            raise RuntimeError(f"unsafe archive member path: {member.name}")
    archive.extractall(destination)


def _load_gsm8k(root: Path) -> Iterable[EvaluationSample]:
    path = root / get_dataset_spec("gsm8k").local_path
    if not path.exists():
        raise FileNotFoundError("GSM8K is not prepared. Run: evalhub prepare-dataset gsm8k")

    with path.open("r", encoding="utf-8") as file:
        for index, line in enumerate(file, start=1):
            row = json.loads(line)
            reference = _extract_gsm8k_answer(row["answer"])
            prompt = (
                "Solve the following grade-school math problem. "
                "Return the final answer as a single number.\n\n"
                f"Problem: {row['question']}\n\nFinal answer:"
            )
            yield EvaluationSample(
                id=f"gsm8k_test_{index}",
                input=prompt,
                reference=reference,
                metadata={"dataset": "gsm8k", "raw_question": row["question"]},
            )


def _extract_gsm8k_answer(answer: str) -> str:
    match = re.search(r"####\s*([-+]?\d[\d,]*(?:\.\d+)?)", answer)
    if not match:
        raise ValueError(f"cannot extract GSM8K final answer: {answer[:120]}")
    return match.group(1).replace(",", "")


def _load_mmlu(root: Path, subjects: list[str]) -> Iterable[EvaluationSample]:
    base_dir = root / get_dataset_spec("mmlu").local_path
    if not base_dir.exists():
        raise FileNotFoundError("MMLU is not prepared. Run: evalhub prepare-dataset mmlu")

    for subject in subjects:
        path = base_dir / f"{subject}_test.csv"
        if not path.exists():
            raise FileNotFoundError(f"MMLU subject file not found: {path}")
        with path.open("r", encoding="utf-8") as file:
            reader = csv.reader(file)
            for index, row in enumerate(reader, start=1):
                if len(row) < 6:
                    continue
                question, option_a, option_b, option_c, option_d, answer = row[:6]
                prompt = (
                    "Answer the multiple-choice question. "
                    "Return only one letter: A, B, C, or D.\n\n"
                    f"Subject: {subject.replace('_', ' ')}\n"
                    f"Question: {question}\n"
                    f"A. {option_a}\n"
                    f"B. {option_b}\n"
                    f"C. {option_c}\n"
                    f"D. {option_d}\n\n"
                    "Answer:"
                )
                yield EvaluationSample(
                    id=f"mmlu_{subject}_{index}",
                    input=prompt,
                    reference=answer.strip().upper(),
                    metadata={"dataset": "mmlu", "subject": subject, "question": question},
                )


def _limited(samples: Iterable[EvaluationSample], limit: int | None) -> list[EvaluationSample]:
    output: list[EvaluationSample] = []
    for sample in samples:
        output.append(sample)
        if limit is not None and len(output) >= limit:
            break
    return output
