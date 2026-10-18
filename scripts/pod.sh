#!/usr/bin/env bash
# Train on a RunPod GPU (see scripts/runpod.py): copy the code, set up, train one arm, and copy results back.
#   scripts/pod.sh IP PORT sync                 copy the repo (no venvs, artifacts or old runs) to /workspace/verity
#   scripts/pod.sh IP PORT setup                Python env (TRL 1.16, vLLM 0.31), shop data, model download
#   scripts/pod.sh IP PORT setup-serve          a lighter env for the live demo server only (no vLLM; any CUDA 12.8+ GPU
#                                               with 24 GB), Stockfish, cloudflared, data, model download
#   scripts/pod.sh IP PORT train ARM [STEPS] [NAME]   train one arm in the background: /workspace/runs/$ENV_NAME/NAME
#                                               (NAME defaults to ARM; use e.g. smoke-ARM for a timing run)
#   scripts/pod.sh IP PORT status               last log lines and steps done
#   scripts/pod.sh IP PORT pull DEST            copy the run (adapter, telemetry, run.json) to DEST/
#   scripts/pod.sh IP PORT evaluate ARGS...     evaluate.py on the pod (runs must be there)
#   scripts/pod.sh IP PORT serve TOKEN          the live demo server (dashboard/live.py) behind a Cloudflare tunnel;
#                                               prints the tunnel's URL (Vercel's INFERENCE_URL)
# Settings (environment variables): ENV_NAME (shop), MODEL, SIGNATURES, LEARNING_RATE, BETA, LORA_R, BATCH_SIZE,
# GRADIENT_ACCUMULATION, GENERATIONS, MAX_COMPLETION_LENGTH, SAVE_STEPS, VLLM_MEMORY, SEED; RUNS (the pod's folder for
# runs, default /workspace/runs/$ENV_NAME); INIT_ADAPTER (continue from an adapter on the pod); for serve, SHOP_RUNS
# (default /workspace/runs/shop).
set -euo pipefail
IP=$1 PORT=$2 COMMAND=$3
shift 3
SSH=(ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -p "$PORT" "root@$IP")
ENV_NAME=${ENV_NAME:-shop}
MODEL=${MODEL:-Qwen/Qwen3-8B}
REMOTE=/workspace/verity
RUNS=${RUNS:-/workspace/runs/$ENV_NAME}
ON_POD="cd $REMOTE && export HF_HOME=/workspace/hf PATH=/workspace/venv/bin:\$PATH PYTHONUNBUFFERED=1"

case "$COMMAND" in
  sync)  # the files git tracks (no old audit runs), plus uncommitted changes to them
    "${SSH[@]}" "command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq rsync >/dev/null)"
    cd "$(dirname "$0")/.."
    git ls-files | grep -v '^experiments/runs/' | rsync -az --files-from=- -e "ssh -o StrictHostKeyChecking=accept-new -p $PORT" \
      ./ "root@$IP:$REMOTE/"
    rsync -az -e "ssh -o StrictHostKeyChecking=accept-new -p $PORT" .git "root@$IP:$REMOTE/" ;;
  setup)
    "${SSH[@]}" "set -e; pip install -q uv && uv venv -q --python 3.12 /workspace/venv && \
      VIRTUAL_ENV=/workspace/venv uv pip install -q 'vllm==0.31.0' 'trl==1.16.0' 'peft==0.21.1' 'datasets==5.0.1' \
        'transformers==5.17.0' 'accelerate>=1.10.1' python-chess && \
      $ON_POD && python -m environments.shop build && \
      python -c \"import torch, vllm, trl, transformers; print('cuda', torch.cuda.is_available(), torch.__version__, \
        'vllm', vllm.__version__, 'trl', trl.__version__, 'transformers', transformers.__version__)\" && \
      python -c \"from huggingface_hub import snapshot_download; snapshot_download('$MODEL')\"" ;;
  setup-serve)
    "${SSH[@]}" "set -e; apt-get update -qq && apt-get install -y -qq stockfish >/dev/null; \
      test -f /workspace/cloudflared || (curl -sL -o /workspace/cloudflared \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x /workspace/cloudflared); \
      pip install -q uv && uv venv -q --python 3.12 /workspace/venv && \
      VIRTUAL_ENV=/workspace/venv uv pip install -q 'torch==2.8.0' 'transformers==5.20.1' 'peft==0.21.2' 'accelerate==1.15.0' python-chess && \
      $ON_POD && python -m environments.shop build && \
      python -c \"import torch; print('cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0))\" && \
      python -c \"from huggingface_hub import snapshot_download; snapshot_download('$MODEL')\"" ;;
  train)
    ARM=$1 STEPS=${2:-80} NAME=${3:-$1}
    "${SSH[@]}" "$ON_POD && mkdir -p $RUNS && nohup python train.py --env $ENV_NAME --arm $ARM --model $MODEL \
      --signatures ${SIGNATURES:-markers/$ENV_NAME.json} --output $RUNS/$NAME --steps $STEPS --seed ${SEED:-42} \
      --learning-rate ${LEARNING_RATE:-3e-5} --beta ${BETA:-0.04} --lora-r ${LORA_R:-32} --batch-size ${BATCH_SIZE:-4} \
      --gradient-accumulation ${GRADIENT_ACCUMULATION:-16} --generations ${GENERATIONS:-8} \
      --max-completion-length ${MAX_COMPLETION_LENGTH:-3072} --save-steps ${SAVE_STEPS:-10} \
      --vllm --vllm-memory ${VLLM_MEMORY:-0.45} ${INIT_ADAPTER:+--init-adapter $INIT_ADAPTER} > $RUNS/$NAME.log 2>&1 < /dev/null &
      echo started $NAME" ;;
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
  serve)
    TOKEN=$1
    CHESS=/workspace/runs/chess_game-v3 SHOP=${SHOP_RUNS:-/workspace/runs/shop}
    "${SSH[@]}" "$ON_POD && pkill -f '^python dashboard/live.py'; pkill -f '^/workspace/cloudflared'; sleep 2; \
      STOCKFISH=/usr/games/stockfish nohup python dashboard/live.py \
        \$(test -f $CHESS/verity/adapter/adapter_config.json && echo --chess-runs $CHESS) \
        \$(test -f $SHOP/verity/adapter/adapter_config.json && echo --shop-runs $SHOP) \
        --port 8770 --token $TOKEN > /workspace/live.log 2>&1 < /dev/null & \
      nohup /workspace/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8770 > /workspace/tunnel.log 2>&1 < /dev/null & \
      for i in \$(seq 1 60); do grep -q 'Verity live' /workspace/live.log && break; sleep 5; done; tail -n 2 /workspace/live.log; \
      grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /workspace/tunnel.log | head -n 1" ;;
  *)
    echo "unknown command: $COMMAND" >&2; exit 2 ;;
esac
