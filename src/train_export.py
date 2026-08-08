"""학습 패키지 내보내기 / 학습된 모델 가져오기 (Phase 3).

- build_train_package(voice_name): my_voice/<이름> 녹음을 Colab 학습용 zip + 노트북으로 묶는다.
- import_model_package(zip_path): Colab에서 받은 모델 zip 을 my_voice_models/<이름>/ 에 설치한다.
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
    """학습 패키지 zip 과 Colab 노트북 2종(Qwen3-TTS / GPT-SoVITS)을 생성한다.

    반환: (zip 경로, [노트북 경로 목록])
    """
    items = _read_metadata(voice_name)
    if len(items) < 5:
        raise ValueError(f"녹음이 {len(items)}개뿐입니다. 학습에는 최소 5개(전체 30개 권장)가 필요해요.")

    os.makedirs(EXPORT_DIR, exist_ok=True)
    zip_path = os.path.join(EXPORT_DIR, f"{voice_name}_train_package.zip")
    notebook_path = os.path.join(EXPORT_DIR, f"{voice_name}_colab_train.ipynb")
    sovits_notebook_path = os.path.join(EXPORT_DIR, f"{voice_name}_colab_train_sovits.ipynb")

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

    _write_notebook(notebook_path, voice_name)
    _write_sovits_notebook(sovits_notebook_path, voice_name, ref_fname, ref_text)
    return zip_path, [notebook_path, sovits_notebook_path]


# 공식 sft_12hz.py 를 기반으로 한 수정판 학습 스크립트.
# 수정점: ① 0.6B 모델의 text_hidden_size(2048)≠hidden_size(1024) 차이를
#   text_projection 으로 해소, ② T4 미지원 flash-attn → sdpa,
#   ③ HF 모델 ID 를 로컬로 받아 체크포인트 저장(copytree) 가능하게, ④ tensorboard 로거 제거.
SFT_SCRIPT = '''import argparse
import json
import os
import shutil

import torch
from accelerate import Accelerator
from dataset import TTSDataset
from huggingface_hub import snapshot_download
from qwen_tts.inference.qwen3_tts_model import Qwen3TTSModel
from safetensors.torch import save_file
from torch.optim import AdamW
from torch.utils.data import DataLoader
from transformers import AutoConfig

target_speaker_embedding = None


def train():
    global target_speaker_embedding

    parser = argparse.ArgumentParser()
    parser.add_argument("--init_model_path", type=str, default="Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    parser.add_argument("--output_model_path", type=str, default="output")
    parser.add_argument("--train_jsonl", type=str, required=True)
    parser.add_argument("--batch_size", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--num_epochs", type=int, default=3)
    parser.add_argument("--speaker_name", type=str, default="speaker_test")
    args = parser.parse_args()

    accelerator = Accelerator(gradient_accumulation_steps=4, mixed_precision="bf16")

    MODEL_PATH = args.init_model_path
    if not os.path.isdir(MODEL_PATH):
        MODEL_PATH = snapshot_download(MODEL_PATH)

    qwen3tts = Qwen3TTSModel.from_pretrained(
        MODEL_PATH,
        torch_dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    config = AutoConfig.from_pretrained(MODEL_PATH)

    train_data = open(args.train_jsonl).readlines()
    train_data = [json.loads(line) for line in train_data]
    dataset = TTSDataset(train_data, qwen3tts.processor, config)
    train_dataloader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, collate_fn=dataset.collate_fn)

    optimizer = AdamW(qwen3tts.model.parameters(), lr=args.lr, weight_decay=0.01)

    model, optimizer, train_dataloader = accelerator.prepare(
        qwen3tts.model, optimizer, train_dataloader
    )

    num_epochs = args.num_epochs
    model.train()

    for epoch in range(num_epochs):
        for step, batch in enumerate(train_dataloader):
            with accelerator.accumulate(model):

                input_ids = batch["input_ids"]
                codec_ids = batch["codec_ids"]
                ref_mels = batch["ref_mels"]
                text_embedding_mask = batch["text_embedding_mask"]
                codec_embedding_mask = batch["codec_embedding_mask"]
                attention_mask = batch["attention_mask"]
                codec_0_labels = batch["codec_0_labels"]
                codec_mask = batch["codec_mask"]

                speaker_embedding = model.speaker_encoder(ref_mels.to(model.device).to(model.dtype)).detach()
                if target_speaker_embedding is None:
                    target_speaker_embedding = speaker_embedding

                input_text_ids = input_ids[:, :, 0]
                input_codec_ids = input_ids[:, :, 1]

                input_codec_embedding = model.talker.model.codec_embedding(input_codec_ids) * codec_embedding_mask
                input_text_embedding = model.talker.model.text_embedding(input_text_ids)
                # 0.6B: text_hidden_size != hidden_size → 추론과 동일하게 투영
                if input_text_embedding.shape[-1] != input_codec_embedding.shape[-1]:
                    input_text_embedding = model.talker.text_projection(input_text_embedding)
                input_text_embedding = input_text_embedding * text_embedding_mask
                input_codec_embedding[:, 6, :] = speaker_embedding

                input_embeddings = input_text_embedding + input_codec_embedding

                for i in range(1, 16):
                    codec_i_embedding = model.talker.code_predictor.get_input_embeddings()[i - 1](codec_ids[:, :, i])
                    codec_i_embedding = codec_i_embedding * codec_mask.unsqueeze(-1)
                    input_embeddings = input_embeddings + codec_i_embedding

                outputs = model.talker(
                    inputs_embeds=input_embeddings[:, :-1, :],
                    attention_mask=attention_mask[:, :-1],
                    labels=codec_0_labels[:, 1:],
                    output_hidden_states=True,
                )

                hidden_states = outputs.hidden_states[0][-1]
                talker_hidden_states = hidden_states[codec_mask[:, :-1]]
                talker_codec_ids = codec_ids[codec_mask]

                sub_talker_logits, sub_talker_loss = model.talker.forward_sub_talker_finetune(talker_codec_ids, talker_hidden_states)

                loss = outputs.loss + 0.3 * sub_talker_loss

                accelerator.backward(loss)

                if accelerator.sync_gradients:
                    accelerator.clip_grad_norm_(model.parameters(), 1.0)

                optimizer.step()
                optimizer.zero_grad()

            if step % 10 == 0:
                accelerator.print(f"Epoch {epoch} | Step {step} | Loss: {loss.item():.4f}")

        # 중간 저장은 시간·디스크 낭비가 커서 마지막 epoch 만 저장한다
        if accelerator.is_main_process and epoch == num_epochs - 1:
            output_dir = os.path.join(args.output_model_path, f"checkpoint-epoch-{epoch}")
            accelerator.print("[저장] 모델 파일 복사 중... (수 분 걸릴 수 있음)")
            shutil.copytree(MODEL_PATH, output_dir, dirs_exist_ok=True)

            input_config_file = os.path.join(MODEL_PATH, "config.json")
            output_config_file = os.path.join(output_dir, "config.json")
            with open(input_config_file, "r", encoding="utf-8") as f:
                config_dict = json.load(f)
            config_dict["tts_model_type"] = "custom_voice"
            talker_config = config_dict.get("talker_config", {})
            talker_config["spk_id"] = {
                args.speaker_name: 3000
            }
            talker_config["spk_is_dialect"] = {
                args.speaker_name: False
            }
            config_dict["talker_config"] = talker_config

            with open(output_config_file, "w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)

            accelerator.print("[저장] 학습된 가중치 추출 중...")
            unwrapped_model = accelerator.unwrap_model(model)
            state_dict = {k: v.detach().to("cpu") for k, v in unwrapped_model.state_dict().items()}

            drop_prefix = "speaker_encoder"
            keys_to_drop = [k for k in state_dict.keys() if k.startswith(drop_prefix)]
            for k in keys_to_drop:
                del state_dict[k]

            weight = state_dict["talker.model.codec_embedding.weight"]
            state_dict["talker.model.codec_embedding.weight"][3000] = target_speaker_embedding[0].detach().to(weight.device).to(weight.dtype)
            save_path = os.path.join(output_dir, "model.safetensors")
            save_file(state_dict, save_path)
            accelerator.print(f"[저장] 완료: {output_dir}")

    accelerator.print("학습이 모두 끝났습니다!")


if __name__ == "__main__":
    train()
'''


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
            "EPOCHS = 15   # loss 가 계속 내려가면 20까지 늘려도 됨 (너무 크면 과적합)\n"
            "BATCH_SIZE = 2\n"
            "LR = 1e-5\n"
            "!nvidia-smi -L"
        ),
        code(
            "!pip install -q qwen-tts\n"
            "!apt-get -qq install -y sox > /dev/null\n"
            "# 이미 클론되어 있으면 건너뜀 (셀 재실행 대비)\n"
            "![ -d Qwen3-TTS ] || git clone -q https://github.com/QwenLM/Qwen3-TTS.git\n"
            "print('설치 완료')"
        ),
        code("%%writefile Qwen3-TTS/finetuning/sft_custom.py\n" + SFT_SCRIPT),
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
            "# 학습 스크립트는 24kHz 만 지원 → 전체 리샘플링\n"
            "import librosa, soundfile as sf, os\n"
            "for f in sorted(os.listdir('data/wavs')):\n"
            "    p = os.path.join('data/wavs', f)\n"
            "    y, _sr = librosa.load(p, sr=24000, mono=True)\n"
            "    sf.write(p, y, 24000)\n"
            "print('24kHz 리샘플링 완료')"
        ),
        code(
            "# 오디오 경로를 절대경로로 변환 (실행 위치와 무관하게 동작하도록)\n"
            "import json, os\n"
            "items = [json.loads(l) for l in open('data/train_raw.jsonl', encoding='utf-8') if l.strip()]\n"
            "for it in items:\n"
            "    it['audio'] = os.path.abspath(it['audio'])\n"
            "    it['ref_audio'] = os.path.abspath(it['ref_audio'])\n"
            "with open('data/train_abs.jsonl', 'w', encoding='utf-8') as f:\n"
            "    f.write('\\n'.join(json.dumps(it, ensure_ascii=False) for it in items) + '\\n')\n"
            "print('변환 완료:', len(items), '개')"
        ),
        code(
            "# 1) 데이터 전처리 (오디오 → 코드 변환)\n"
            "!python Qwen3-TTS/finetuning/prepare_data.py \\\n"
            "  --device cuda:0 \\\n"
            "  --tokenizer_model_path Qwen/Qwen3-TTS-Tokenizer-12Hz \\\n"
            "  --input_jsonl data/train_abs.jsonl \\\n"
            "  --output_jsonl data/train_with_codes.jsonl"
        ),
        code(
            "# 2) 파인튜닝 (SFT — 0.6B 호환 수정판 스크립트)\n"
            "!python Qwen3-TTS/finetuning/sft_custom.py \\\n"
            "  --init_model_path {MODEL} \\\n"
            "  --output_model_path output \\\n"
            "  --train_jsonl data/train_with_codes.jsonl \\\n"
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


def _write_sovits_notebook(path: str, voice_name: str, ref_fname: str, ref_text: str) -> None:
    """GPT-SoVITS v2 파인튜닝 Colab 노트북 생성 (Qwen3-TTS 와 품질 비교용).

    같은 학습 패키지 zip 을 사용하며, 노트북 안에서 테스트 합성까지 들어볼 수 있다.
    """

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
            f"`{voice_name}_train_package.zip` 을 선택하세요 (Qwen3-TTS 용과 같은 zip).\n\n"
            "- 학습이 끝나면 테스트 합성을 노트북 안에서 바로 들어볼 수 있습니다\n"
            "- 마지막 셀이 가중치 패키지 zip 을 자동 다운로드합니다\n"
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
            "!pip install -q -r requirements.txt\n"
            "!apt-get -qq install -y ffmpeg sox > /dev/null\n"
            "print('설치 완료')"
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
            "yaml.dump(cfg, open('TEMP/tmp_s1.yaml', 'w'), default_flow_style=False)\n"
            "os.environ['hz'] = '25hz'\n"
            "os.environ['_CUDA_VISIBLE_DEVICES'] = '0'\n"
            "!python GPT_SoVITS/s1_train.py --config_file TEMP/tmp_s1.yaml"
        ),
        code(
            "# 4) 테스트 합성 — 노트북에서 바로 들어보기 (실패해도 패키징은 가능)\n"
            "import glob, os, traceback\n"
            "sovits_w = sorted(glob.glob(f'SoVITS_weights_v2/{EXP}*.pth'), key=os.path.getmtime)[-1]\n"
            "gpt_w = sorted(glob.glob(f'GPT_weights_v2/{EXP}*.ckpt'), key=os.path.getmtime)[-1]\n"
            "print('SoVITS:', sovits_w)\n"
            "print('GPT:', gpt_w)\n"
            f"REF_AUDIO = os.path.abspath('data/wavs/{ref_fname}')\n"
            f"REF_TEXT = {ref_text_py}\n"
            "try:\n"
            "    from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config\n"
            "    c = TTS_Config('GPT_SoVITS/configs/tts_infer.yaml')\n"
            "    c.device = 'cuda'\n"
            "    c.is_half = True\n"
            "    pipe = TTS(c)\n"
            "    pipe.init_t2s_weights(gpt_w)\n"
            "    pipe.init_vits_weights(sovits_w)\n"
            "    gen = pipe.run({'text': '지피티 소비츠로 학습한 목소리 테스트입니다. 자연스럽게 들리나요?',\n"
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
