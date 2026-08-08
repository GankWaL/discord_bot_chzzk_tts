"""학습 패키지 내보내기 / 학습된 모델 가져오기 (Phase 3).

- build_train_package(voice_name): my_voice/<이름> 녹음을 Colab 학습용 zip + 노트북으로 묶는다.
- import_model_package(zip_path): Colab에서 받은 모델 zip 을 my_voice_models/<이름>/ 에 설치한다.
"""

import json
import os
import shutil
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MY_VOICE_DIR = os.path.join(BASE_DIR, "my_voice")
MY_VOICE_MODELS_DIR = os.path.join(BASE_DIR, "my_voice_models")
EXPORT_DIR = os.path.join(BASE_DIR, "my_voice_export")


def _read_metadata(voice_name: str) -> list:
    """[(wav 절대경로, 파일명, 문장)] 목록."""
    dataset = os.path.join(MY_VOICE_DIR, voice_name)
    items = []
    with open(os.path.join(dataset, "metadata.csv"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "|" not in line:
                continue
            fname, _, text = line.partition("|")
            wav = os.path.join(dataset, "wavs", fname)
            if os.path.exists(wav):
                items.append((wav, fname, text))
    return items


def build_train_package(voice_name: str) -> tuple:
    """학습 패키지 zip 과 Colab 노트북을 생성하고 (zip 경로, 노트북 경로)를 반환한다."""
    items = _read_metadata(voice_name)
    if len(items) < 5:
        raise ValueError(f"녹음이 {len(items)}개뿐입니다. 학습에는 최소 5개(전체 30개 권장)가 필요해요.")

    os.makedirs(EXPORT_DIR, exist_ok=True)
    zip_path = os.path.join(EXPORT_DIR, f"{voice_name}_train_package.zip")
    notebook_path = os.path.join(EXPORT_DIR, f"{voice_name}_colab_train.ipynb")

    # 참조 음성: 가장 긴(파일이 큰) 녹음
    ref_fname = max(items, key=lambda it: os.path.getsize(it[0]))[1]

    # 학습용 JSONL (zip 내부 상대 경로 기준)
    jsonl_lines = [
        json.dumps(
            {"audio": f"./data/wavs/{fname}", "text": text, "ref_audio": f"./data/wavs/{ref_fname}"},
            ensure_ascii=False,
        )
        for _wav, fname, text in items
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for wav, fname, _text in items:
            zf.write(wav, f"data/wavs/{fname}")
        zf.writestr("data/train_raw.jsonl", "\n".join(jsonl_lines) + "\n")
        zf.writestr(
            "data/meta.json",
            json.dumps({"voice_name": voice_name, "samples": len(items)}, ensure_ascii=False),
        )

    _write_notebook(notebook_path, voice_name)
    return zip_path, notebook_path


def _write_notebook(path: str, voice_name: str) -> None:
    """업로드→학습→다운로드까지 자동 진행되는 Colab 노트북 생성."""

    def code(source: str) -> dict:
        return {"cell_type": "code", "metadata": {}, "outputs": [], "execution_count": None,
                "source": source.splitlines(keepends=True)}

    def md(source: str) -> dict:
        return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}

    cells = [
        md(
            f"# 내 목소리 학습 — {voice_name}\n\n"
            "**사용법**: 메뉴에서 *런타임 → 모두 실행* 을 누르고, 파일 업로드 창이 뜨면 "
            f"`{voice_name}_train_package.zip` 을 선택하세요. 학습이 끝나면 모델 zip 이 자동 다운로드됩니다.\n\n"
            "- 무료 Colab(T4)은 0.6B 모델 기준입니다. Colab Pro(A100)라면 아래 `MODEL` 을 1.7B 로 바꿔도 됩니다.\n"
            "- 소요 시간: 약 20~40분 (데이터 양에 따라)"
        ),
        code(
            'MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"  # Colab Pro(A100)라면 "Qwen/Qwen3-TTS-12Hz-1.7B-Base"\n'
            f'SPEAKER_NAME = "{voice_name}"\n'
            "EPOCHS = 5\n"
            "BATCH_SIZE = 2\n"
            "LR = 5e-6\n"
            "!nvidia-smi -L"
        ),
        code(
            "!pip install -q qwen-tts\n"
            "!git clone -q https://github.com/QwenLM/Qwen3-TTS.git\n"
            "print('설치 완료')"
        ),
        code(
            "from google.colab import files\n"
            "print('학습 패키지 zip 을 업로드하세요...')\n"
            "uploaded = files.upload()\n"
            "zip_name = list(uploaded)[0]\n"
            "!unzip -qo \"{zip_name}\"\n"
            "!ls data/wavs | head -5\n"
            "print('업로드/압축해제 완료')"
        ),
        code(
            "# 1) 데이터 전처리 (오디오 → 코드 변환)\n"
            "!cd Qwen3-TTS/finetuning && python prepare_data.py \\\n"
            "  --device cuda:0 \\\n"
            "  --tokenizer_model_path Qwen/Qwen3-TTS-Tokenizer-12Hz \\\n"
            "  --input_jsonl ../../data/train_raw.jsonl \\\n"
            "  --output_jsonl ../../data/train_with_codes.jsonl"
        ),
        code(
            "# 2) 파인튜닝 (SFT)\n"
            "!cd Qwen3-TTS/finetuning && python sft_12hz.py \\\n"
            "  --init_model_path {MODEL} \\\n"
            "  --output_model_path ../../output \\\n"
            "  --train_jsonl ../../data/train_with_codes.jsonl \\\n"
            "  --batch_size {BATCH_SIZE} \\\n"
            "  --lr {LR} \\\n"
            "  --num_epochs {EPOCHS} \\\n"
            "  --speaker_name \"{SPEAKER_NAME}\""
        ),
        code(
            "# 3) 마지막 체크포인트를 모델 패키지로 묶어 다운로드\n"
            "import glob, json, os, shutil\n"
            "ckpts = sorted(glob.glob('output/checkpoint-epoch-*'), key=lambda p: int(p.rsplit('-', 1)[1]))\n"
            "last = ckpts[-1]\n"
            "print('선택된 체크포인트:', last)\n"
            "with open(os.path.join(last, 'meta.json'), 'w', encoding='utf-8') as f:\n"
            "    json.dump({'voice_name': SPEAKER_NAME, 'speaker': SPEAKER_NAME, 'base_model': MODEL}, f, ensure_ascii=False)\n"
            f"shutil.make_archive('{voice_name}_model_package', 'zip', last)\n"
            "from google.colab import files\n"
            f"files.download('{voice_name}_model_package.zip')"
        ),
    ]
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "colab": {"provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "accelerator": "GPU",
        },
        "cells": cells,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notebook, f, ensure_ascii=False, indent=1)


def import_model_package(zip_path: str) -> str:
    """모델 패키지 zip 을 my_voice_models/<이름>/ 에 설치하고 목소리 이름을 반환한다."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "meta.json" not in names:
            raise ValueError("meta.json 이 없는 zip 입니다. Colab 노트북이 만든 모델 패키지를 선택해주세요.")
        meta = json.loads(zf.read("meta.json").decode("utf-8"))
        voice_name = meta.get("voice_name") or os.path.splitext(os.path.basename(zip_path))[0]

        target = os.path.join(MY_VOICE_MODELS_DIR, voice_name)
        if os.path.exists(target):
            shutil.rmtree(target)
        os.makedirs(target, exist_ok=True)
        zf.extractall(target)
    return voice_name
