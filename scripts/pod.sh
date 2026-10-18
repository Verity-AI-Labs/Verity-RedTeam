#!/usr/bin/env bash
# Train on a RunPod GPU (see scripts/runpod.py): copy the code, set up, train one arm, and copy results back.
#   scripts/pod.sh IP PORT sync                 copy the repo (no venvs, artifacts or old runs) to /workspace/verity
#   scripts/pod.sh IP PORT setup                Python env (TRL 1.16, vLLM 0.31), shop data, model download
#   scripts/pod.sh IP PORT train ARM [STEPS]    train one arm in the background: /workspace/runs/$ENV_NAME/ARM
#   scripts/pod.sh IP PORT status               last log lines and steps done
#   scripts/pod.sh IP PORT pull DEST            copy the run (adapter, telemetry, run.json) to DEST/
#   scripts/pod.sh IP PORT evaluate ARGS...     evaluate.py on the pod (runs must be there)
# Settings (environment variables): ENV_NAME (shop), MODEL, SIGNATURES, LEARNING_RATE, LORA_R, BATCH_SIZE,
# GRADIENT_ACCUMULATION, GENERATIONS, MAX_COMPLETION_LENGTH, SAVE_STEPS, VLLM_MEMORY, SEED.
set -euo pipefail
IP=$1 PORT=$2 COMMAND=$3
shift 3
SSH=(ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -p "$PORT" "root@$IP")
ENV_NAME=${ENV_NAME:-shop}
MODEL=${MODEL:-Qwen/Qwen3-8B}
REMOTE=/workspace/verity
RUNS=/workspace/runs/$ENV_NAME
ON_POD="cd $REMOTE && export HF_HOME=/workspace/hf PATH=/workspace/venv/bin:\$PATH PYTHONUNBUFFERED=1"

case "$COMMAND" in
  sync)
    rsync -az -e "ssh -o StrictHostKeyChecking=accept-new -p $PORT" --delete \
      --exclude '.venv*' --exclude artifacts/ --exclude experiments/runs/ --exclude data/ --exclude logs/ \
      --exclude '.agent-*' --exclude __pycache__ --exclude .env --exclude 'dashboard/build/' \
      "$(dirname "$0")/../" "root@$IP:$REMOTE/" ;;
  setup)
    "${SSH[@]}" "set -e; pip install -q uv && uv venv -q --python 3.12 /workspace/venv && \
      VIRTUAL_ENV=/workspace/venv uv pip install -q 'vllm==0.31.0' 'trl==1.16.0' 'peft==0.21.1' 'datasets==5.0.1' \
        'transformers==5.17.0' 'accelerate>=1.10.1' python-chess && \
      $ON_POD && python -m environments.shop build && \
      python -c \"import torch, vllm, trl, transformers; print('cuda', torch.cuda.is_available(), torch.__version__, \
        'vllm', vllm.__version__, 'trl', trl.__version__, 'transformers', transformers.__version__)\" && \
      python -c \"from huggingface_hub import snapshot_download; snapshot_download('$MODEL')\"" ;;
  train)
    ARM=$1 STEPS=${2:-80}
    "${SSH[@]}" "$ON_POD && mkdir -p $RUNS && nohup python train.py --env $ENV_NAME --arm $ARM --model $MODEL \
      --signatures ${SIGNATURES:-markers/$ENV_NAME.json} --output $RUNS/$ARM --steps $STEPS --seed ${SEED:-42} \
      --learning-rate ${LEARNING_RATE:-5e-5} --lora-r ${LORA_R:-32} --batch-size ${BATCH_SIZE:-4} \
      --gradient-accumulation ${GRADIENT_ACCUMULATION:-16} --generations ${GENERATIONS:-8} \
      --max-completion-length ${MAX_COMPLETION_LENGTH:-3072} --save-steps ${SAVE_STEPS:-10} \
      --vllm --vllm-memory ${VLLM_MEMORY:-0.35} > $RUNS/$ARM.log 2>&1 &
      echo started $ARM" ;;
  status)
    "${SSH[@]}" "tail -n 5 $RUNS/*.log; for run in $RUNS/*/run.json; do python3 -c \"import json,sys; r=json.load(open(sys.argv[1])); \
      print(sys.argv[1], r['status'], r['steps_completed'], 'steps', r.get('wall_seconds'), 's', r.get('error', ''))\" \$run; done; \
      nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader" ;;
  pull)
    DEST=$1
    mkdir -p "$DEST"
    rsync -az -e "ssh -o StrictHostKeyChecking=accept-new -p $PORT" --exclude 'checkpoint-*/optimizer.pt' \
      --exclude 'checkpoint-*/scheduler.pt' --exclude 'checkpoint-*/rng_state.pth' "root@$IP:$RUNS/" "$DEST/" ;;
  evaluate)
    "${SSH[@]}" "$ON_POD && python evaluate.py --env $ENV_NAME $*" ;;
  *)
    echo "unknown command: $COMMAND" >&2; exit 2 ;;
esac
