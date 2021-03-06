import numpy as np
import tensorflow as tf
from tensorflow import bool as boolean
from tensorflow import float32, int32

from nets.net import Net
from nets.utils import (copy_net_op, discount, get_trainable_vars,
                        prep_data_cells, prep_data_grids)


class RQNet(Net):
    def __init__(self, name, *args, **kwargs):
        """
        Lagging Double QNet. Can do supervised learning, Q-Learning, SARSA.
        Optionally duelling architecture.
        """
        super().__init__(name=name, *args, **kwargs)
        self.sess.run(self.copy_online_to_target)

    def _build_base_net_rnn(self, inp, name):
        n_units = 256
        with tf.variable_scope('model/' + name):
            hidden = tf.layers.dense(inp, n_units, tf.nn.relu)
            # Recurrent network for temporal dependencies
            lstm_cell = tf.contrib.rnn.BasicLSTMCell(n_units, state_is_tuple=True)
            c_init = np.zeros((1, lstm_cell.state_size.c), np.float32)
            h_init = np.zeros((1, lstm_cell.state_size.h), np.float32)
            state_init = [c_init, h_init]
            c_in = tf.placeholder(tf.float32, [1, lstm_cell.state_size.c])
            h_in = tf.placeholder(tf.float32, [1, lstm_cell.state_size.h])
            state_in = (c_in, h_in)
            rnn_in = tf.expand_dims(hidden, [0])
            step_size = tf.shape(inp)[:1]
            state_in = tf.contrib.rnn.LSTMStateTuple(c_in, h_in)
            lstm_outputs, lstm_state = tf.nn.dynamic_rnn(
                lstm_cell,
                rnn_in,
                initial_state=state_in,
                sequence_length=step_size,
                time_major=False)
            lstm_c, lstm_h = lstm_state
            state_out = (lstm_c[:1, :], lstm_h[:1, :])
            out = tf.reshape(lstm_outputs, [-1, n_units])
        return (out, state_init, state_in, state_out)

    def _build_head(self, inp, name):
        with tf.variable_scope('model/' + name) as scope:
            if self.pp['dueling_qnet']:
                h1 = inp
                # h1 = tf.layers.dense(
                #     inputs=base_net,
                #     units=140,
                #     kernel_initializer=self.kern_init_dense(),
                #     use_bias=False,
                #     name="h1")
                value = tf.layers.dense(
                    inputs=h1,
                    units=1,
                    kernel_initializer=self.kern_init_dense(),
                    use_bias=False,
                    name="value")
                advantages = tf.layers.dense(
                    inputs=h1,
                    units=self.n_channels,
                    use_bias=False,
                    kernel_initializer=self.kern_init_dense(),
                    name="advantages")
                # Avg. dueling supposedly more stable than max according to paper
                # Max Dueling
                # q_vals = value + (advantages - tf.reduce_max(
                #     advantages, axis=1, keepdims=True))
                # Average Dueling
                q_vals = value + (
                    advantages - tf.reduce_mean(advantages, axis=1, keepdims=True))
                if "online" in name:
                    self.advantages = advantages
                # if "target" in name:
                #     self.value = value
            else:
                q_vals = tf.layers.dense(
                    inputs=inp,
                    units=self.n_channels,
                    kernel_initializer=self.kern_init_dense(),
                    kernel_regularizer=self.regularizer,
                    use_bias=False,
                    name="q_vals")
            trainable_vars_by_name = get_trainable_vars(scope)
        return q_vals, trainable_vars_by_name

    def build(self):
        # Create input placeholders
        depth = self.n_channels * 2 if self.grid_split else self.n_channels
        gridshape = [None, self.rows, self.cols, depth]
        frepshape = [None, self.rows, self.cols, self.n_channels + 1]
        oh_cellshape = [None, self.rows, self.cols, 1]  # Onehot
        self.grids = tf.placeholder(boolean, gridshape, "grid")
        self.freps = tf.placeholder(int32, frepshape, "frep")
        self.oh_cells = tf.placeholder(boolean, oh_cellshape, "oh_cell")
        self.chs = tf.placeholder(int32, [None], "ch")
        self.q_targets = tf.placeholder(float32, [None], "qtarget")
        if self.pp['batch_size'] > 1:
            self.weights = tf.placeholder(float32, [None], "qtarget")
        else:
            self.weights = 1

        # Prepare inputs for network
        grids_f = tf.cast(self.grids, float32)
        freps_f = tf.cast(self.freps, float32)
        oh_cells_f = tf.cast(self.oh_cells, float32)
        mult1 = np.ones(frepshape[1:], np.float32)
        mult1[:, :, :-1] /= 43
        mult1[:, :, -1] /= 70
        tmult1 = tf.constant(mult1)
        freps_f = freps_f * tmult1
        nrange = tf.range(tf.shape(self.grids)[0])
        # numbered_chs: [[0, ch0], [1, ch1], [2, ch2], ..., [n, ch_n]]
        numbered_chs = tf.stack([nrange, self.chs], axis=1)

        # Create online and target networks with optional RNN
        online_base_net = self._build_base_net(
            grids_f, freps_f, oh_cells_f, name="q_networks/online")
        orrn = self._build_base_net_rnn(online_base_net, name="q_networks/rnn/online")
        # (Net output, rnn input state, rnn input state placeholder, rnn output state)
        (online_net, self.online_state, self.online_state_in,
         self.online_state_out) = orrn

        self.online_q_vals, online_vars = self._build_head(
            online_net, name="q_networks/online")
        self.copy_online_to_target = tf.no_op()

        # Maximum valued ch from online network
        self.online_q_amax = tf.argmax(
            self.online_q_vals, axis=1, name="online_q_amax", output_type=int32)
        # Target Q-value for greedy channel as selected by online network
        numbered_q_amax = tf.stack([nrange, self.online_q_amax], axis=1)
        self.online_q_max = tf.gather_nd(self.online_q_vals, numbered_q_amax)
        # Target Q-value for given ch
        self.online_q_selected = tf.gather_nd(self.online_q_vals, numbered_chs)
        # Online Q-value for given ch
        online_q_selected = tf.gather_nd(self.online_q_vals, numbered_chs)

        self.td_err = self.q_targets - online_q_selected
        if self.pp['huber_loss'] is not None:
            # Linear when loss is above delta and squared difference below
            self.loss = tf.losses.huber_loss(
                labels=self.q_targets,
                predictions=online_q_selected,
                delta=self.pp['huber_loss'],
                weights=self.weights)
        else:
            # Sum of squares difference between the target and prediction Q values.
            self.loss = tf.losses.mean_squared_error(
                labels=self.q_targets,
                predictions=online_q_selected,
                weights=self.weights)
        return online_vars

    def forward(self, grid, cell, ce_type, frep=None):
        data = {
            self.grids: prep_data_grids(grid, split=self.grid_split),
            self.oh_cells: prep_data_cells(cell),
            self.online_state_in: self.online_state
        }
        if frep is not None:
            data[self.freps] = [frep]
        if self.pp['dueling_qnet']:
            q_vals_op = self.advantages
        else:
            q_vals_op = self.online_q_vals
        q_vals, self.online_state = self.sess.run(
            [q_vals_op, self.online_state_out],
            data,
            options=self.options,
            run_metadata=self.run_metadata)
        q_vals = q_vals[0]
        assert q_vals.shape == (self.n_channels, ), f"{q_vals.shape}\n{q_vals}"
        return q_vals

    def _backward(self, data) -> (float, float):
        data[self.online_state_in] = self.online_state
        # data[self.online_state_in] = self.online_state
        _, loss, lr, td_err, self.online_state = self.sess.run(
            [self.do_train, self.loss, self.lr, self.td_err, self.online_state_out],
            feed_dict=data,
            options=self.options,
            run_metadata=self.run_metadata)
        return loss, lr, td_err

    def backward_supervised(self, grids, cells, chs, q_targets, freps=None, weights=None):
        data = {
            self.grids: prep_data_grids(grids, self.grid_split),
            self.oh_cells: prep_data_cells(cells),
            self.chs: chs,
            self.q_targets: q_targets,
        }
        if freps is not None:
            data[self.freps] = freps
        if weights is not None:
            data[self.weights] = weights
        return self._backward(data)

    def _double_q_target(self, grids, cells, freps=None, target_chs=None) -> [float]:
        """Find bootstrap value, i.e. Q(Stn, A; Wt).
        where Stn: state at time t+n
              A: target_chs, if specified, else argmax(Q(Stn, a; Wo))
              n: usually 1, unless n-step Q-learning
              Wo/Wt: online/target network"""
        data = {
            self.grids: prep_data_grids(grids, self.grid_split),
            self.oh_cells: prep_data_cells(cells)
        }
        data[self.online_state_in] = self.online_state
        if target_chs is None:
            # Greedy Q-Learning
            target_q = self.online_q_max
        else:
            # SARSA or Eligible Q-learning
            target_q = self.online_q_selected
            data[self.chs] = target_chs
        if freps is not None:
            data[self.freps] = freps
        qvals, self.online_state = self.sess.run([target_q, self.online_state_out], data)
        return qvals

    def backward(self,
                 grids,
                 cells,
                 chs,
                 rewards,
                 next_grids,
                 next_cells,
                 gamma,
                 freps=None,
                 next_freps=None,
                 next_chs=None,
                 weights=None) -> (float, float):
        """
        Supports n-step learning where (grids, cells) is from time t
        and (next_grids, next_cells) is from time t+n
        Support greedy action selection if 'next_chs' is None
        Feature representations (freps) of grids are optional
        """
        next_qvals = self._double_q_target(next_grids, next_cells, next_freps, next_chs)
        q_targets = next_qvals
        for reward in rewards[::-1]:
            q_targets = reward + gamma * q_targets
        return self.backward_supervised(grids, cells, chs, q_targets, freps, weights)

    def backward_multi_nstep(self,
                             grids,
                             cells,
                             chs,
                             rewards,
                             next_grid,
                             next_cell,
                             gamma,
                             next_ch=None) -> (float, float):
        """
        Multi n-step. Train on n-step, (n-1)-step, (n-2)-step, ..., 1-step returns
        """
        next_qvals = self._double_q_target(next_grid, next_cell, next_ch)
        rewards_plus = np.asarray(rewards + [next_qvals])
        # q_targets:
        # [(r0 + g*r1 + g**2*r2 +..+ g**n*q_n), (r1 + g*r2 +..+ g**(n-1)*q_n), ..,
        # (r(n-1) + g*q_n)] where g: gamma, q_n: next_qval (bootstrap val)
        q_targets = discount(rewards_plus, gamma)[:-1]
        return self.backward_supervised(grids, cells, chs, q_targets)

    def backward_gae(self,
                     grids,
                     cells,
                     chs,
                     rewards,
                     next_grid,
                     next_cell,
                     gamma,
                     next_ch=None) -> (float, float):
        """Generalized Advantage Estimation"""
        next_qvals = self._double_q_target(next_grid, next_cell, next_ch)
        vals = self.sess.run(
            self.target_q_max, {
                self.grids: prep_data_grids(grids, self.grid_split),
                self.oh_cells: prep_data_cells(cells),
                self.chs: chs
            })
        value_plus = np.zeros((len(vals) + 1))
        value_plus[:len(vals)] = vals
        value_plus[-1] = next_qvals
        advantages = discount(rewards + gamma * value_plus[1:] - value_plus[:-1], gamma)
        return self.backward_supervised(grids, cells, chs, q_targes=advantages)
