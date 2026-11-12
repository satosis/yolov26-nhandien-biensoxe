import os
import sys
import types
import logging
import importlib
import importlib.util

logger = logging.getLogger("inference.ort_utils")

ort = importlib.import_module("onnxruntime") if importlib.util.find_spec("onnxruntime") else None
_HAS_ORT = ort is not None


def _patch_e2e_loss():
    class E2ELoss:
        pass

    try:
        import ultralytics.utils.loss as u_loss
        u_loss.E2ELoss = E2ELoss
    except Exception:
        pass
    try:
        import ultralytics.nn.modules.loss as m_loss
        m_loss.E2ELoss = E2ELoss
    except Exception:
        pass

    for name in ("ultralytics.utils.loss", "ultralytics.nn.modules.loss"):
        module = sys.modules.setdefault(name, types.ModuleType(name))
        module.E2ELoss = E2ELoss


def _onnx_runtime_target(path: str, available_providers, *, explicit_model: bool = False, prefer_float: bool = False):
    """Choose an available accelerator and a compatible plate model."""
    available = set(available_providers)
    gpu = [name for name in ("TensorrtExecutionProvider", "CUDAExecutionProvider")
           if name in available]
    if (gpu or prefer_float) and not explicit_model and path.lower().endswith("_int8.onnx"):
        candidate = path[:-len("_int8.onnx")] + ".onnx"
        if os.path.isfile(candidate):
            path = candidate
    if path.lower().endswith("_int8.onnx"):
        gpu = []
    providers = gpu
    if not gpu and "CoreMLExecutionProvider" in available and "int8" not in path.lower():
        providers = ["CoreMLExecutionProvider"]
    providers.append("CPUExecutionProvider")
    return path, providers


def _plate_ort_session_options(native_threads=None):
    """Bound detector CPU workers when the caller or video worker requests it."""
    if not _HAS_ORT:
        return None
    value = native_threads if native_threads is not None else os.environ.get("VIDEO_OCR_NATIVE_THREADS")
    if value is None:
        return None
    try:
        threads = int(value)
    except ValueError:
        logger.warning("Ignoring invalid VIDEO_OCR_NATIVE_THREADS=%r", value)
        return None
    if not 1 <= threads <= 64:
        logger.warning("Ignoring out-of-range VIDEO_OCR_NATIVE_THREADS=%r", value)
        return None
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return options
