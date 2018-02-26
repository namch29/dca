import numpy as np
import tensorflow as tf
from tensorflow import bool as boolean
from tensorflow import float32, int32

from nets.qnet import QNet
from nets.utils import get_trainable_vars


class BigHeadQNet(QNet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _build_net(self, grids, freps, ncells, name):
        with tf.variable_scope('model/' + name) as scope:
            conv1 = tf.layers.conv2d(
                inputs=grids,
                filters=140,
                kernel_size=5,
                padding="same",
                kernel_initializer=self.kern_init_conv(),
                kernel_regularizer=self.regularizer,
                use_bias=False,
                activation=self.act_fn)
            conv2 = tf.layers.conv2d(
                inputs=conv1,
                filters=70,
                kernel_size=3,
                padding="same",
                kernel_initializer=self.kern_init_conv(),
                kernel_regularizer=self.regularizer,
                use_bias=True,
                activation=self.act_fn)
            conv3 = tf.layers.conv2d(
                inputs=conv2,
                filters=70,
                kernel_size=1,
                padding="same",
                kernel_initializer=self.kern_init_conv(),
                kernel_regularizer=self.regularizer,
                use_bias=True,
                activation=self.act_fn)
            q_vals = tf.gather_nd(conv3, ncells)
            trainable_vars_by_name = get_trainable_vars(scope)
        return q_vals, trainable_vars_by_name
