from strats import strat_classes

import argparse
import logging

import numpy as np


def get_pparams():
    """
    Problem parameters
    """
    parser = argparse.ArgumentParser(
        description='DCA',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    stratnames = [n[0].lower() for n in strat_classes()]
    stratnames += ['show', 'random', 'fixed']

    parser.add_argument(
        '--strat',
        choices=stratnames,
        default='fixed')
    parser.add_argument(
        '--rows', type=int, help="number of rows in grid", default=7)
    parser.add_argument(
        '--cols', type=int, help="number of cols in grid", default=7)
    parser.add_argument(
        '--n_channels', type=int, help="number of channels", default=70)
    parser.add_argument(
        '--erlangs',
        type=int,
        help="erlangs = call_rate * call_duration"
        "\n 10 erlangs = 200 call rate, given 3 call duration"
        "\n 7.5 erlangs = 150cr, 3cd"
        "\n 5 erlangs = 100cr, 3cd",
        default=10)
    parser.add_argument(
        '--call_rates', type=int, help="in calls per minute", default=None)
    parser.add_argument(
        '--call_duration', type=int, help="in minutes", default=3)
    parser.add_argument(
        '--p_handoff', type=float, help="handoff probability", default=0.15)
    parser.add_argument(
        '--hoff_call_duration',
        type=int,
        help="handoff call duration, in minutes",
        default=1)
    parser.add_argument(
        '--n_events',
        type=int,
        help="number of events to simulate",
        default=200000)
    parser.add_argument(
        '--avg_runs',
        type=int,
        help="Run simulation 'n' times, report average scores",
        default=1)

    parser.add_argument(
        '--alpha', type=float, help="(RL) learning rate",
        default=0.036)
    parser.add_argument(
        '--alpha_decay',
        type=float,
        help="(RL) factor by which alpha is multiplied each iter",
        default=0.999998)
    parser.add_argument(
        '--epsilon',
        type=float,
        help="(RL) probability of choosing random action",
        default=0.75443)
    parser.add_argument(
        '--epsilon_decay',
        type=float,
        help="(RL) factor by which epsilon is multiplied each iter",
        default=0.99999)
    parser.add_argument(
        '--gamma', type=float, help="(RL) discount factor",
        default=0.85)
    parser.add_argument(
        '--save_exp_data',
        action='store_true',
        default=False)
    parser.add_argument(
        '--hopt',
        action='store_true',
        help="Override default params by sampling in logspace",  # noqa
        default=False)
    parser.add_argument(
        '--hopt_best',
        action='store_true',
        help="Show best params found and corresponding loss for a given strat",  # noqa
        default=False)

    parser.add_argument(
        '--net_lr',
        type=float,
        help="(Net) Learning rate",
        default=9e-5)
    parser.add_argument(
        '--batch_size',
        type=int,
        help="(Net) Batch size for experience replay",
        default=10)
    parser.add_argument(
        '--bench_batch_size',
        action='store_true',
        help="(Net) Benchmark batch size for neural network",
        default=False)
    parser.add_argument(
        '--train_net',
        action='store_true',
        help="(Net) Train network",
        default=False)
    parser.add_argument(
        '--verify_grid',
        action='store_true',
        help="verify reuse constraint each iteration",
        default=False)
    parser.add_argument(
        '--prof',
        dest='profiling',
        action='store_true',
        help="performance profiling",
        default=False)
    parser.add_argument('--gui', action='store_true', default=False)
    parser.add_argument(
        '--plot', action='store_true', dest='do_plot', default=False)
    parser.add_argument(
        '--log_level',
        type=int,
        help="10: Debug,\n20: Info,\n30: Warning",
        default=logging.INFO)
    parser.add_argument(
        '--log_file', type=str,
        help="enable logging to file by entering file name")
    parser.add_argument(
        '--log_iter',
        type=int,
        help="Show blocking probability every n iterations",
        default=50000)

    # iterations can be approximated from hours with:
    # iters = 7821* hours - 2015
    args = parser.parse_args()
    params = vars(args)

    # Sensible presets / overrides
    if "net" in params['strat'].lower():
        params['log_iter'] = 5000
    else:
        params['batch_size'] = 1
    if not params['call_rates']:
        params['call_rates'] = params['erlangs'] / params['call_duration']
    if params['avg_runs'] > 1:
        params['gui'] = False
        params['log_level'] = logging.ERROR
    if params['hopt']:
        params['log_level'] = logging.ERROR
    if params['bench_batch_size']:
        params['log_level'] = logging.WARN

    return params


def non_uniform_preset(pp):
    raise NotImplementedError  # Untested
    """
    Non-uniform traffic patterns for linear array of cells.
    Formål: How are the different strategies sensitive to
    non-uniform call patterns?
    rows = 1
    cols = 20
    call rates: l:low, m:medium, h:high
    For each pattern, the numeric values of l, h and m are chosen
    so that the average call rate for a cell is 120 calls/hr.
    low is 1/3 of high; med is 2/3 of high.
    """
    avg_cr = 120 / 60  # 120 calls/hr
    patterns = ["mmmm" * 5,
                "lhlh" * 5,
                ("llh" * 7)[:20],
                ("hhl" * 7)[:20],
                ("lhl" * 7)[:20],
                ("hlh" * 7)[:20]]
    pattern_call_rates = []
    for pattern in patterns:
        n_l = pattern.count('l')
        n_m = pattern.count('m')
        n_h = pattern.count('h')
        cr_h = avg_cr * 20 / (n_h + 2 / 3 * n_m + 1 / 3 * n_l)
        cr_m = 2 / 3 * cr_h
        cr_l = 1 / 3 * cr_h
        call_rates = np.zeros((1, 20))
        for i, c in enumerate(pattern):
            if c == 'l':
                call_rates[0][i] = cr_l
            elif c == 'm':
                call_rates[0][i] = cr_m
            elif c == 'h':
                call_rates[0][i] = cr_h
        pattern_call_rates.append(call_rates)
