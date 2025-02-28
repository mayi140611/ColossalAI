import random

import pytest
import torch
from einops import rearrange

from colossalai.kernel.cuda_native.mha.flash_attn_2 import HAS_FLASH_ATTN
from colossalai.kernel.cuda_native.mha.mem_eff_attn import HAS_MEM_EFF_ATTN
from colossalai.testing import clear_cache_before_run, parameterize

if HAS_MEM_EFF_ATTN or HAS_FLASH_ATTN:
    from colossalai.kernel.cuda_native import ColoAttention
    from colossalai.kernel.cuda_native.scaled_softmax import AttnMaskType

DTYPE = [torch.float16, torch.bfloat16, torch.float32]


def baseline_attention(Z, N_CTX, H, q, k, v, sm_scale):
    M = torch.tril(torch.ones((N_CTX, N_CTX), device="cuda"))
    p = torch.matmul(q, k.transpose(2, 3)) * sm_scale
    for z in range(Z):
        for h in range(H):
            p[:, :, M == 0] = float("-inf")
    p = torch.softmax(p.float(), dim=-1).half()
    ref_out = torch.matmul(p, v)
    return ref_out


@pytest.mark.skipif(not HAS_MEM_EFF_ATTN and not HAS_FLASH_ATTN, reason="xformers is not available")
@clear_cache_before_run()
@parameterize('proj_shape', [(1, 8, 4, 16)])
@parameterize('dtype', DTYPE)
def test_attention_gpt(proj_shape, dtype):
    # TODO check output value
    (B, S, H, D_HEAD) = proj_shape
    D = H * D_HEAD

    c_attn = torch.nn.Linear(D, 3 * D, dtype=dtype, device="cuda")
    attn = ColoAttention(D, H, dropout=0.1)

    x = torch.randn((B, S, D), dtype=dtype, device="cuda")

    qkv = c_attn(x)
    q, k, v = rearrange(qkv, 'b s (n h d) -> n b s h d', n=3, h=H)

    mask = [torch.ones(S - i, dtype=dtype, device="cuda") for i in range(B)]
    mask = torch.nn.utils.rnn.pad_sequence(mask, batch_first=True)

    y = attn(q, k, v, attn_mask=mask, attn_mask_type=AttnMaskType.paddedcausal)

    assert list(y.shape) == [B, S, D]

    dy = torch.rand_like(y)
    y.backward(dy)


@pytest.mark.skipif(not HAS_MEM_EFF_ATTN and not HAS_FLASH_ATTN, reason="xformers is not available")
@clear_cache_before_run()
@parameterize('proj_shape', [(6, 8, 4, 16)])
@parameterize('dtype', DTYPE)
def test_attention_bert(proj_shape, dtype):
    (B, S, H, D_HEAD) = proj_shape
    D = H * D_HEAD

    c_attn = torch.nn.Linear(D, 3 * D, dtype=dtype, device="cuda")
    attn = ColoAttention(D, H, dropout=0.1)

    x = torch.randn((B, S, D), dtype=dtype, device="cuda")
    # attention mask of shape [B, S] with zero padding to max length S
    mask = [torch.ones(S - i, dtype=dtype, device="cuda") for i in range(B)]
    mask = torch.nn.utils.rnn.pad_sequence(mask, batch_first=True)

    qkv = c_attn(x)
    q, k, v = rearrange(qkv, 'b s (n h d) -> b s n h d', n=3, h=H).unbind(dim=2)
    y = attn(q, k, v, attn_mask=mask, attn_mask_type=AttnMaskType.padding)

    assert list(y.shape) == [B, S, D]

    dy = torch.rand_like(y)
    y.backward(dy)


@pytest.mark.skipif(not HAS_MEM_EFF_ATTN and not HAS_FLASH_ATTN, reason="xformers is not available")
@clear_cache_before_run()
@parameterize('proj_shape', [(6, 8, 4, 16)])
@parameterize('dtype', DTYPE)
def test_attention_no_mask(proj_shape, dtype):
    (B, S, H, D_HEAD) = proj_shape
    D = H * D_HEAD

    c_attn = torch.nn.Linear(D, 3 * D, dtype=dtype, device="cuda")
    attn = ColoAttention(D, H, dropout=0.1)

    x = torch.randn((B, S, D), dtype=dtype, device="cuda")
    qkv = c_attn(x)
    q, k, v = rearrange(qkv, 'b s (n h d) -> b s n h d', n=3, h=H).unbind(dim=2)
    y = attn(q, k, v)

    assert list(y.shape) == [B, S, D]

    dy = torch.rand_like(y)
    y.backward(dy)


@pytest.mark.skipif(not HAS_MEM_EFF_ATTN and not HAS_FLASH_ATTN, reason="xformers is not available")
@clear_cache_before_run()
@parameterize('proj_shape', [(6, 24, 8, 4, 16)])
@parameterize('dtype', DTYPE)
def test_cross_attention(proj_shape, dtype):
    (B, S, T, H, D_HEAD) = proj_shape
    D = H * D_HEAD

    q_attn = torch.nn.Linear(D, D, dtype=dtype, device="cuda")
    kv_attn = torch.nn.Linear(D, 2 * D, dtype=dtype, device="cuda")

    attn = ColoAttention(D, H, dropout=0.1)

    src = torch.randn((B, S, D), dtype=dtype, device="cuda")
    tgt = torch.randn((B, T, D), dtype=dtype, device="cuda")

    q = q_attn(tgt)
    kv = kv_attn(src)
    q = rearrange(q, 'b s (h d) -> b s h d', h=H)
    k, v = rearrange(kv, 'b s (n h d) -> b s n h d', n=2, h=H).unbind(dim=2)
    y = attn(q, k, v, attn_mask_type=AttnMaskType.causal)

    assert list(y.shape) == [B, T, D]

    dy = torch.rand_like(y)
    y.backward(dy)
