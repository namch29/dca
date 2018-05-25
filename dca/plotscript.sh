#!/bin/bash
# Comment out to avoid running
# runsim=""
runplot=""

# NOTE Current .1 results updates avg reward every iter
# Should do an additional 16 runs of
# targ-avg
# grads-semi
# grads-avg
# grads-resid
# vnet
# hla-vnet
# final-nohoff-vnet

targs=""
grads=""
hla=""
finalhoff=""
finalnohoff=""

# Whether to run non-VNets or not
# nonvnets=""

# events=100000
events=470000

# logiter=5000
logiter=25000

avg=32
ext=".4"

sarsa_dir="sarsas/"
fixed_dir="fixed/"
vnet_dir=""


runargs=(--log_iter "${logiter}"
         --log_level 30
         --avg_runs "${avg}"
         -i "${events}"
         --log_file "plots/plot-log${ext}"
         -epol greedy
         --breakout_thresh 0.4)

## TARGETS ##
if [ -v targs ]; then
    if [ -v runsim ]; then
        # # SMDP Discount
        python3 main.py singhnet "${runargs[@]}" \
                -save_bp targ-smdp --target discount -rtype smdp_callcount \
                -phoff --beta_disc --beta 21 -lr 5.1e-6 || exit 1
        # # MDP Discount
        python3 main.py singhnet "${runargs[@]}" \
                -phoff -save_bp targ-mdp --target discount \
                --net_lr 2.02e-7 || exit 1
        # MDP Average
        python3 main.py singhnet "${runargs[@]}" \
                -phoff -save_bp targ-avg \
                --net_lr 3.43e-06 --weight_beta 0.00368 || exit 1
    fi
    if [ -v runplot ]; then
        python3 plotter.py "${vnet_dir}targ-smdp" "${vnet_dir}targ-mdp" "${vnet_dir}targ-avg" \
                --labels "SMDP discount (SB-VNet)" "MDP discount" "MDP avg. rewards" \
                --title "Target comparison (with hand-offs)" \
                --ext $ext --ctype new hoff tot --plot_save targets --ymins 10 5 10 || exit 1
    fi
    echo "Finished targets"
fi

if [ -v grads ]; then
    ## GRADIENTS (no hoffs) ##
    if [ -v runsim ]; then
        # Semi-grad A-MDP (same as "MDP Average", without hoffs)
        python3 main.py singhnet "${runargs[@]}" \
                -save_bp grads-semi  --net_lr 3.43e-06 --weight_beta 0.00368 || exit 1
        # Residual grad A-MDP
        python3 main.py residsinghnet "${runargs[@]}" \
                -save_bp grads-resid --net_lr 1.6e-05 || exit 1
        #  TDC A-MDP
        python3 main.py tftdcsinghnet "${runargs[@]}" \
                -save_bp grads-tdc || exit 1
        # #  TDC MDP Discount
        python3 main.py tftdcsinghnet "${runargs[@]}" \
                -save_bp grads-tdc-gam --target discount \
                --net_lr 1.91e-06 --grad_beta 5e-09 || exit 1
    fi
    if [ -v runplot ]; then
        python3 plotter.py "${vnet_dir}grads-semi" "${vnet_dir}grads-resid" \
                "${vnet_dir}grads-tdc" "${vnet_dir}grads-tdc-gam" \
                --labels "Semi-grad. (A-MDP)" "Residual grad. (A-MDP)" \
                "TDC (A-MDP) (AA-VNet)" "TDC (MDP)" \
                --title "Gradient comparison (no hand-offs)" \
                --ext $ext --ctype new --plot_save grads --ymins 5 || exit 1
    fi
    echo "Finished Grads"
fi

## Exploration RS-SARSA ##
# if [ -v runsim ]; then
#     # RS-SARSA 
#     python3 main.py rs_sarsa --log_iter $logiter --avg_runs $avg $runt \
#             -save_bp hla-rssarsa --target discount -phoff 0.15 -hla || exit 1
# fi
# python3 plotter.py "exp-rssarsa-greedy" "exp-rssarsa-boltzlo" "exp-rssarsa-boltzhi" \
#         --labels "RS-SARSA Greedy" "RS-SARSA Boltmann Low" "RS-SARSA Boltmann High" \
#         --title "Exploration for state-action methods" \
#         --ctype new hoff --plot_save exp-rssarsa || exit 1

## Exploration VNet ##
# if [ -v runsim ]; then
#     # VNet greedy
#     python3 main.py rs_sarsa --log_iter $logiter --avg_runs $avg $runt \
#             -save_bp exp-vnet-greedy --target discount -phoff 0.15 -hla || exit 1
# fi
# python3 plotter.py "exp-vnet-greedy" "exp-vnet-boltzlo" "exp-vnet-boltzhi" \
#         --labels "VNet Greedy" "VNet Boltmann Low" "VNet Boltmann High" \
#         --title "Exploration for state methods" \
#         --ext $ext --ctype new hoff --plot_save exp-vnet || exit 1

if [ -v hla ]; then
    ## HLA ##
    if [ -v runsim ]; then
        if [ -v nonvnets ]; then
            # # RS-SARSA
            python3 main.py rs_sarsa "${runargs[@]}" \
                    -save_bp rssarsa --lilith -phoff || exit 1
            # # RS-SARSA HLA
            python3 main.py hla_rs_sarsa "${runargs[@]}" \
                    -save_bp hla-rssarsa --lilith -phoff -hla || exit 1
        fi
        #  TDC A-MDP
        python3 main.py tftdcsinghnet "${runargs[@]}" \
                -save_bp vnet -phoff || exit 1
        # TDC A-MDP HLA
        python3 main.py tftdcsinghnet "${runargs[@]}" \
                -save_bp hla-vnet -hla -phoff || exit 1
    fi
    if [ -v runplot ]; then
        python3 plotter.py "${vnet_dir}vnet" "${vnet_dir}hla-vnet" \
                "${sarsa_dir}rssarsa" "${sarsa_dir}hla-rssarsa" \
                --labels "AA-VNet" "AA-VNet (HLA)" "RS-SARSA" "RS-SARSA (HLA)" \
                --title "Hand-off look-ahead" \
                --ext $ext --ctype new hoff tot --plot_save hla --ymins 10 0 10 || exit 1
    fi
    echo "Finished HLA"
fi

if [ -v finalhoff ]; then
    ## Final comparison ##
    # VNet and HLA from previous run
    if [ -v runsim ] && [ -v nonvnets ]; then
        for i in {5..10}
        do
            # FCA
            python3 main.py fixedassign "${runargs[@]}" \
                    -save_bp "final-fca-e${i}" -phoff --erlangs $i || exit 1
            # RandomAssign
            python3 main.py randomassign "${runargs[@]}" \
                    -save_bp "final-rand-e${i}" -phoff --erlangs $i  || exit 1
        done
        # Erlangs = 10 already done in previous run
        for i in {5..9}
        do
        #  TDC A-MDP
        python3 main.py tftdcsinghnet "${runargs[@]}" \
                -save_bp vnet -phoff --erlangs $i || exit 1
        # TDC A-MDP HLA
        python3 main.py tftdcsinghnet "${runargs[@]}" \
                -save_bp hla-vnet -hla -phoff --erlangs $i || exit 1
        done
    fi


    if [ -v runplot ]; then
        python3 plotter.py "${vnet_dir}hla-vnet" "${sarsa_dir}hla-rssarsa" \
                "${fixed_dir}final-fca" "${fixed_dir}final-rand" \
                --labels "AA-VNet (HLA)" "RS-SARSA (HLA)" "FCA" "Random assignment" \
                --title "RL vs non-learning agents (with hand-offs)" \
                --erlangs --ext $ext --ctype new hoff tot --plot_save final-whoff --ymins 10 0 10 || exit 1
    fi
    echo "Finished Final w/hoff"
fi

if [ -v finalnohoff ]; then
    ## Final comparison, without hoffs
    if [ -v runsim ]; then
        for i in {5..10}
        do
            if [ -v nonvnets ] ; then
                # FCA
                python3 main.py fixedassign "${runargs[@]}" \
                        -save_bp "final-fca-e${i}" -phoff --erlangs $i || exit 1
                # RandomAssign
                python3 main.py randomassign "${runargs[@]}" \
                        -save_bp "final-rand-e${i}" -phoff --erlangs $i  || exit 1
                # RS-SARSA
                python3 main.py rs_sarsa "${runargs[@]}" \
                        -save_bp final-nohoff-rssarsa --lilith --erlangs $i || exit 1
                # FCA
                python3 main.py fixedassign "${runargs[@]}" \
                        -save_bp final-nohoff-fca --erlangs $i || exit 1
                # RandomAssign
                python3 main.py randomassign "${runargs[@]}" \
                        -save_bp final-nohoff-rand --erlangs $i || exit 1
            fi
            # TDC avg.
            python3 main.py tftdcsinghnet "${runargs[@]}" \
                    -save_bp final-nohoff-vnet --erlangs $i || exit 1
        done
    fi
    if [ -v runplot ]; then
        python3 plotter.py "${vnet_dir}final-nohoff-vnet" "${sarsa_dir}final-nohoff-rssarsa" \
                "${fixed_dir}final-nohoff-fca" "${fixed_dir}final-nohoff-rand" \
                --labels "AA-VNet" "RS-SARSA" "FCA" "Random assignment" \
                --title "RL vs non-learning agents (no hand-offs)" \
                --erlangs --ext $ext --ctype new --plot_save final-nohoff --ymins 10 || exit 1
    fi
    echo "Finished Final wo/hoff"
fi
echo "FINISHED ALL"