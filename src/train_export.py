"""학습 패키지 내보내기 / 학습된 모델 가져오기 (GPT-SoVITS v2).

- build_train_package(voice_name): my_voice/<이름> 녹음을 Colab 학습용 zip + 노트북으로 묶는다.
- import_model_package(zip_path): Colab에서 받은 모델 zip(sovits.pth + gpt.ckpt)을
  my_voice_models/<이름>/ 에 설치한다.
"""

import json
import os
import shutil
import sys
import zipfile

# 프로젝트 루트: exe(PyInstaller)면 실행 파일 위치 기준(dist 의 상위), 아니면 src/ 의 상위
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    if os.path.basename(BASE_DIR).lower() == "dist":
        BASE_DIR = os.path.dirname(BASE_DIR)
else:
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
    """학습 패키지 zip 과 Colab 노트북(GPT-SoVITS)을 생성한다.

    반환: (zip 경로, [노트북 경로 목록])
    """
    items = _read_metadata(voice_name)
    if len(items) < 5:
        raise ValueError(f"녹음이 {len(items)}개뿐입니다. 학습에는 최소 5개(전체 30개 권장)가 필요해요.")

    os.makedirs(EXPORT_DIR, exist_ok=True)
    zip_path = os.path.join(EXPORT_DIR, f"{voice_name}_train_package.zip")
    notebook_path = os.path.join(EXPORT_DIR, f"{voice_name}_colab_train_sovits.ipynb")

    # 참조 음성: 가장 긴(파일이 큰) 녹음
    ref_item = max(items, key=lambda it: os.path.getsize(it[0]))
    ref_fname, ref_text = ref_item[1], ref_item[2]

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

    _write_sovits_notebook(notebook_path, voice_name, ref_fname, ref_text)
    return zip_path, [notebook_path]


def _write_sovits_notebook(path: str, voice_name: str, ref_fname: str, ref_text: str) -> None:
    """GPT-SoVITS v2 파인튜닝 Colab 노트북 생성."""

    def code(source: str) -> dict:
        return {"cell_type": "code", "metadata": {}, "outputs": [], "execution_count": None,
                "source": source.splitlines(keepends=True)}

    def md(source: str) -> dict:
        return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}

    ref_text_py = json.dumps(ref_text, ensure_ascii=False)

    cells = [
        md(
            f"# 내 목소리 학습 (GPT-SoVITS v2) — {voice_name}\n\n"
            "**사용법**: *런타임 → 모두 실행* 후 파일 업로드 창이 뜨면 "
            f"`{voice_name}_train_package.zip` 을 선택하세요.\n\n"
            "- 학습이 끝나면 테스트 합성을 노트북 안에서 바로 들어볼 수 있습니다\n"
            "- 마지막 셀이 가중치 패키지 zip 을 자동 다운로드합니다\n"
            "- 받은 zip 은 GUI 의 [학습된 모델 가져오기] 로 등록하세요\n"
            "- 소요 시간: 환경 설치 포함 약 30~60분"
        ),
        code(
            f'SPEAKER_NAME = "{voice_name}"\n'
            "SOVITS_EPOCHS = 8\n"
            "GPT_EPOCHS = 15\n"
            "BATCH_SIZE = 4\n"
            "!nvidia-smi -L"
        ),
        code(
            "import os\n"
            "![ -d GPT-SoVITS ] || git clone -q https://github.com/RVC-Boss/GPT-SoVITS.git\n"
            "if os.path.basename(os.getcwd()) != 'GPT-SoVITS':\n"
            "    os.chdir('GPT-SoVITS')\n"
            "# requirements 설치 (실패 시 마지막 줄에 오류가 보이도록 tail 유지)\n"
            "!pip install -q -r requirements.txt 2>&1 | tail -3\n"
            "# requirements 가 조용히 누락시키는 패키지 + 한국어 g2p 세트 명시 설치\n"
            "!pip install -q ffmpeg-python x_transformers jamo g2pk2 ko_pron pytorch-lightning\n"
            "# opencc 는 소스 빌드가 실패할 수 있어 순수 파이썬 구현으로 대체 (중국어용, 한국어 학습엔 미사용)\n"
            "!python -c 'import opencc' 2>/dev/null || pip install -q opencc-python-reimplemented\n"
            "!apt-get -qq install -y ffmpeg sox > /dev/null\n"
            "# 필수 모듈 설치 검증\n"
            "import importlib\n"
            "missing = []\n"
            "for m in ['ffmpeg', 'x_transformers', 'jamo', 'g2pk2', 'ko_pron', 'torchaudio', 'transformers', 'pytorch_lightning']:\n"
            "    try:\n"
            "        importlib.import_module(m)\n"
            "    except Exception as e:\n"
            "        missing.append(f'{m}: {e}')\n"
            "print('설치 완료' if not missing else '누락 발견:\\n' + '\\n'.join(missing))"
        ),
        code(
            "# 사전학습(파운데이션) 모델 다운로드\n"
            "from huggingface_hub import snapshot_download\n"
            "snapshot_download(\n"
            "    'lj1995/GPT-SoVITS',\n"
            "    local_dir='GPT_SoVITS/pretrained_models',\n"
            "    allow_patterns=[\n"
            "        'gsv-v2final-pretrained/*',\n"
            "        'chinese-hubert-base/*',\n"
            "        'chinese-roberta-wwm-ext-large/*',\n"
            "    ],\n"
            ")\n"
            "print('사전학습 모델 준비 완료')"
        ),
        code(
            "from google.colab import files\n"
            "print('학습 패키지 zip 을 업로드하세요...')\n"
            "uploaded = files.upload()\n"
            "zip_name = list(uploaded)[0]\n"
            "!unzip -qo \"{zip_name}\"\n"
            "print('업로드/압축해제 완료')"
        ),
        code(
            "# 학습 리스트(.list) 생성: 경로|화자|언어|문장\n"
            "import json, os\n"
            "items = [json.loads(l) for l in open('data/train_raw.jsonl', encoding='utf-8') if l.strip()]\n"
            "with open('data/train.list', 'w', encoding='utf-8') as f:\n"
            "    for it in items:\n"
            "        f.write(f\"{os.path.abspath(it['audio'])}|{SPEAKER_NAME}|KO|{it['text']}\\n\")\n"
            "print('학습 리스트 생성:', len(items), '문장')"
        ),
        code(
            "# 1) 데이터 전처리 3단계 (텍스트 → HuBERT → 시맨틱 토큰)\n"
            "import os\n"
            "EXP = SPEAKER_NAME\n"
            "opt_dir = f'logs/{EXP}'\n"
            "os.environ.update({\n"
            "    # 전처리 스크립트가 GPT_SoVITS 내부 모듈(text 등)을 찾도록 경로 지정\n"
            "    'PYTHONPATH': f'{os.getcwd()}/GPT_SoVITS:{os.getcwd()}',\n"
            "    'inp_text': 'data/train.list',\n"
            "    'inp_wav_dir': '',\n"
            "    'exp_name': EXP,\n"
            "    'opt_dir': opt_dir,\n"
            "    'bert_pretrained_dir': 'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large',\n"
            "    'cnhubert_base_dir': 'GPT_SoVITS/pretrained_models/chinese-hubert-base',\n"
            "    'pretrained_s2G': 'GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth',\n"
            "    's2config_path': 'GPT_SoVITS/configs/s2.json',\n"
            "    'is_half': 'True', 'i_part': '0', 'all_parts': '1',\n"
            "    '_CUDA_VISIBLE_DEVICES': '0', 'version': 'v2',\n"
            "})\n"
            "!python GPT_SoVITS/prepare_datasets/1-get-text.py\n"
            "!python GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py\n"
            "!python GPT_SoVITS/prepare_datasets/3-get-semantic.py\n"
            "# 분할 결과 병합 (단일 파트)\n"
            "txt0 = f'{opt_dir}/2-name2text-0.txt'\n"
            "if os.path.exists(txt0):\n"
            "    os.replace(txt0, f'{opt_dir}/2-name2text.txt')\n"
            "sem0 = f'{opt_dir}/6-name2semantic-0.tsv'\n"
            "if os.path.exists(sem0):\n"
            "    body = open(sem0, encoding='utf-8').read()\n"
            "    with open(f'{opt_dir}/6-name2semantic.tsv', 'w', encoding='utf-8') as f:\n"
            "        f.write('item_name\\tsemantic_audio\\n' + body)\n"
            "print('전처리 완료:', sorted(os.listdir(opt_dir)))"
        ),
        code(
            "# 2) SoVITS(음색) 학습\n"
            "import json, os\n"
            "cfg = json.load(open('GPT_SoVITS/configs/s2.json'))\n"
            "cfg['train'].update(batch_size=BATCH_SIZE, epochs=SOVITS_EPOCHS, text_low_lr_rate=0.4,\n"
            "    pretrained_s2G='GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth',\n"
            "    pretrained_s2D='GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2D2333k.pth',\n"
            "    if_save_latest=True, if_save_every_weights=True, save_every_epoch=SOVITS_EPOCHS,\n"
            "    gpu_numbers='0', grad_ckpt=False, lora_rank=32)\n"
            "cfg['model']['version'] = 'v2'\n"
            "cfg['data']['exp_dir'] = opt_dir\n"
            "cfg['s2_ckpt_dir'] = opt_dir\n"
            "cfg['save_weight_dir'] = 'SoVITS_weights_v2'\n"
            "cfg['name'] = EXP\n"
            "cfg['version'] = 'v2'\n"
            "os.makedirs('TEMP', exist_ok=True)\n"
            "os.makedirs(f'{opt_dir}/logs_s2_v2', exist_ok=True)  # 체크포인트 저장 폴더 (webui 가 만들어주는 부분)\n"
            "# 이전 실행의 체크포인트가 있으면 '이미 완료'로 보고 즉시 종료해버리므로 정리\n"
            "!rm -f {opt_dir}/logs_s2_v2/G_*.pth {opt_dir}/logs_s2_v2/D_*.pth\n"
            "os.makedirs('SoVITS_weights_v2', exist_ok=True)      # 최종 가중치 저장 폴더\n"
            "os.makedirs('GPT_weights_v2', exist_ok=True)\n"
            "json.dump(cfg, open('TEMP/tmp_s2.json', 'w'))\n"
            "!python GPT_SoVITS/s2_train.py --config TEMP/tmp_s2.json"
        ),
        code(
            "# 3) GPT(운율) 학습\n"
            "import os, yaml\n"
            "cfg = yaml.safe_load(open('GPT_SoVITS/configs/s1longer-v2.yaml'))\n"
            "cfg['train'].update(batch_size=BATCH_SIZE, epochs=GPT_EPOCHS, save_every_n_epoch=GPT_EPOCHS,\n"
            "    if_save_every_weights=True, if_save_latest=True, if_dpo=False,\n"
            "    half_weights_save_dir='GPT_weights_v2', exp_name=EXP)\n"
            "cfg['pretrained_s1'] = 'GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt'\n"
            "cfg['train_semantic_path'] = f'{opt_dir}/6-name2semantic.tsv'\n"
            "cfg['train_phoneme_path'] = f'{opt_dir}/2-name2text.txt'\n"
            "cfg['output_dir'] = f'{opt_dir}/logs_s1_v2'\n"
            "os.makedirs(f'{opt_dir}/logs_s1_v2', exist_ok=True)  # 체크포인트 저장 폴더\n"
            "yaml.dump(cfg, open('TEMP/tmp_s1.yaml', 'w'), default_flow_style=False)\n"
            "os.environ['hz'] = '25hz'\n"
            "os.environ['_CUDA_VISIBLE_DEVICES'] = '0'\n"
            "!python GPT_SoVITS/s1_train.py --config_file TEMP/tmp_s1.yaml"
        ),
        code(
            "# 4) 테스트 합성 — 노트북에서 바로 들어보기 (실패해도 패키징은 가능)\n"
            "import glob, os, traceback\n"
            "sovits_list = sorted(glob.glob(f'SoVITS_weights_v2/{EXP}*.pth'), key=os.path.getmtime)\n"
            "gpt_list = sorted(glob.glob(f'GPT_weights_v2/{EXP}*.ckpt'), key=os.path.getmtime)\n"
            "assert sovits_list, 'SoVITS 가중치가 없습니다 — SoVITS(음색) 학습 셀을 다시 실행해주세요.'\n"
            "assert gpt_list, 'GPT 가중치가 없습니다 — GPT(운율) 학습 셀을 다시 실행해주세요.'\n"
            "sovits_w = sovits_list[-1]\n"
            "gpt_w = gpt_list[-1]\n"
            "print('SoVITS:', sovits_w)\n"
            "print('GPT:', gpt_w)\n"
            f"REF_AUDIO = os.path.abspath('data/wavs/{ref_fname}')\n"
            f"REF_TEXT = {ref_text_py}\n"
            "try:\n"
            "    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config\n"
            "    c = TTS_Config('GPT_SoVITS/configs/tts_infer.yaml')\n"
            "    c.device = 'cuda'\n"
            "    c.is_half = True\n"
            "    c.t2s_weights_path = gpt_w\n"
            "    c.vits_weights_path = sovits_w\n"
            "    pipe = TTS(c)\n"
            "    gen = pipe.run({'text': '학습한 목소리 테스트입니다. 자연스럽게 들리나요?',\n"
            "                    'text_lang': 'ko', 'ref_audio_path': REF_AUDIO,\n"
            "                    'prompt_text': REF_TEXT, 'prompt_lang': 'ko'})\n"
            "    sr, audio = next(gen)\n"
            "    import soundfile as sf\n"
            "    sf.write('sovits_test.wav', audio, sr)\n"
            "    from IPython.display import Audio, display\n"
            "    display(Audio('sovits_test.wav'))\n"
            "except Exception:\n"
            "    traceback.print_exc()\n"
            "    print('테스트 합성 실패 — 아래 셀에서 가중치 패키징은 정상 진행됩니다.')"
        ),
        code(
            "# 5) 가중치 패키지로 묶어 다운로드\n"
            "import json, shutil, os\n"
            "os.makedirs('package', exist_ok=True)\n"
            "shutil.copy(sovits_w, 'package/sovits.pth')\n"
            "shutil.copy(gpt_w, 'package/gpt.ckpt')\n"
            "shutil.copy(REF_AUDIO, 'package/ref.wav')\n"
            "with open('package/meta.json', 'w', encoding='utf-8') as f:\n"
            "    json.dump({'engine': 'gpt-sovits', 'voice_name': SPEAKER_NAME,\n"
            "               'ref_text': REF_TEXT, 'version': 'v2'}, f, ensure_ascii=False)\n"
            f"shutil.make_archive('{voice_name}_sovits_package', 'zip', 'package')\n"
            "from google.colab import files\n"
            f"files.download('{voice_name}_sovits_package.zip')"
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
    """모델 패키지 zip(sovits.pth + gpt.ckpt)을 my_voice_models/<이름>/ 에 설치한다."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for required in ("sovits.pth", "gpt.ckpt", "meta.json"):
            if required not in names:
                raise ValueError(
                    f"{required} 이(가) 없는 zip 입니다. Colab 노트북이 만든 GPT-SoVITS 모델 패키지를 선택해주세요."
                )
        meta = json.loads(zf.read("meta.json").decode("utf-8"))
        voice_name = meta.get("voice_name") or os.path.splitext(os.path.basename(zip_path))[0]

        target = os.path.join(MY_VOICE_MODELS_DIR, voice_name)
        if os.path.exists(target):
            shutil.rmtree(target)
        os.makedirs(target, exist_ok=True)
        zf.extractall(target)
    return voice_name
