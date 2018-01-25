import random

import numpy as np
import tensorflow as tf


def softmax(x, axis=None):
    """Compute softmax values for each sets of scores in x. If axis is not given,
    softmax over the last dimension."""
    if axis is None:
        axis = np.ndim(x) - 1
    # Subtract maximum value for numerical stability; result will be the same
    e_x = np.exp(x - np.max(x, axis=axis, keepdims=True))
    return e_x / np.sum(e_x, axis=axis, keepdims=True)


def discount(rewards, gamma):
    discounted = []
    r = 0
    for reward in rewards[::-1]:
        r = reward + gamma * r
        discounted.append(r)
    return discounted[::-1]


def set_global_seeds(i):
    "Reproducible results"
    tf.set_random_seed(i)
    np.random.seed(i)
    random.seed(i)


# Used to initialize weights for policy and value output layers
def normalized_columns_initializer(std=1.0):
    def _initializer(shape, dtype=None, partition_info=None):
        out = np.random.randn(*shape).astype(np.float32)
        out *= std / np.sqrt(np.square(out).sum(axis=0, keepdims=True))
        return tf.constant(out)

    return _initializer


def get_init_by_name(name):
    if name == "zeros":
        return tf.zeros_initializer
    elif name == "glorot_unif":
        # The default for dense, perhaps for conv2d also. AKA Xavier.
        return tf.glorot_uniform_initializer
    elif name == "glorot_norm":
        return tf.glorot_normal_initializer
    elif name == "norm_cols":
        return normalized_columns_initializer


def get_act_fn_by_name(name):
    if name == "relu":
        return tf.nn.relu
    elif name == "elu":
        return tf.nn.elu
    elif name == "leaky_relu":
        return tf.nn.leaky_relu


def get_optimizer_by_name(name, l_rate):
    if name == "sgd":
        return tf.train.GradientDescentOptimizer(learning_rate=l_rate)
    elif name == "sgd-m":
        return tf.train.MomentumOptimizer(learning_rate=l_rate, momentum=0.95)
    elif name == "adam":
        return tf.train.AdamOptimizer(learning_rate=l_rate)
    elif name == "rmsprop":
        return tf.train.RMSPropOptimizer(learning_rate=l_rate)


def copy_net_op(online_vars, target_vars, tau):
    copy_ops = []
    for var_name, target_var in target_vars.items():
        online_val = online_vars[var_name].value()
        target_val = target_var.value()
        val = online_val * tau + (1 - tau) * target_val
        op = target_var.assign(val)
        copy_ops.append(op)
    return tf.group(*copy_ops)


def prep_data_grids(grids, empty_neg=True):
    """
    empty_neg: Represent empty channels as -1 instead of 0
    """
    assert type(grids) == np.ndarray
    if grids.ndim == 3:
        grids = np.expand_dims(grids, axis=0)
    grids.shape = (-1, 7, 7, 70)
    if empty_neg:
        grids = grids.astype(np.int8)
        # Make empty cells -1 instead of 0.
        # Temporarily convert to int8 to save memory
        grids = grids * 2 - 1
    grids = grids.astype(np.float16)
    return grids


def prep_data_cells(cells):
    if type(cells) == tuple:
        cells = [cells]
    oh_cells = np.zeros((len(cells), 7, 7), dtype=np.float16)
    # One-hot grid encoding
    for i, cell in enumerate(cells):
        oh_cells[i][cell] = 1
    oh_cells.shape = (-1, 7, 7, 1)
    # Should not be used when predicting, but could save mem when training
    # del cells

    return oh_cells


def prep_data(grids, cells, actions, rewards, next_grids, next_cells):
    assert type(actions) == np.ndarray
    assert type(rewards) == np.ndarray
    actions = actions.astype(np.int32)
    rewards = rewards.astype(
        np.float32)  # Needs to be 32-bit, else will overflow

    grids = prep_data_grids(grids)
    next_grids = prep_data_grids(next_grids)
    oh_cells = prep_data_cells(cells)
    next_oh_cells = prep_data_cells(next_cells)
    return grids, oh_cells, actions, rewards, next_grids, next_oh_cells
