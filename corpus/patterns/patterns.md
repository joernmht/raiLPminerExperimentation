# Structural patterns in the canonical models

`patterns-2026.11.1`: 40 models, 146 constraint rows, 47 rows match no pattern. Detection reads structure only (domains, parameter kinds, aggregation, comparator, signs); the annotated kind / domain class is shown for cross-checking and is never used.

## Model-level hits

| Pattern | Models |
|---|---|
| knapsack problem: max weighted 0-1 sum s.t. knapsack rows | - |
| assignment: two exactly-one row families on one variable | assignment, lintim__openlintim__vehicle-scheduling-ip |
| generalized assignment: exactly-one + knapsack on one variable | GEN:jrtpm.2013.10.001, GEN:trb.2023.102786, mip_2_4_time_indexed |
| set covering: min cost s.t. covering rows | lintim__openlintim__line-planning-cost-model, romain-montagne__vrpy__vrp-set-covering-master |
| set partitioning: min cost s.t. partition rows | 10.1016_j.ejor.2021.04.041, GEN:jrtpm.2013.10.001, INFORMSJoC__2024.0698__mdvsp-ts-arc-flow-time-expanded, INFORMSJoC__2024.0698__mdvsp-ts-set-partitioning-master, Yinwenxu-1212__crewScheduling__crew-pairing-set-covering-master, assignment, lintim__openlintim__vehicle-scheduling-ip, mip_2_4_time_indexed |
| set packing: max value s.t. packing rows | - |
| disjunctive scheduling: big-M ordering + y_ij + y_ji = 1 | 10.1016_j.trb.2017.05.008, GEN:omega.2024.103040, mip_2_1_big_m |
| periodic event scheduling (PESP) | mip_2_8_pesp, pesp_solvable |

## Row-level counts

| Pattern | Rows |
|---|---|
| exactly one: sum of 0-1 terms = 1 | 21 |
| at most one: sum of 0-1 terms <= 1 | 4 |
| at least one: sum of 0-1 terms >= 1 | 0 |
| count bound: unit sum of integral terms vs a constant | 11 |
| knapsack: weighted sum of integral terms <= capacity | 1 |
| flow conservation: sum in = sum out of one variable | 1 |
| periodic tension: t_j - t_i + T*k within bounds | 4 |
| big-M switch: binary times a big-M parameter | 8 |
| difference: x - y vs a constant (precedence, headway) | 16 |
| linking: x <= U*y or x >= L*y with a binary switch | 10 |
| bound: one variable vs a constant | 24 |

## Per model

### 10.1016_j.apm.2016.06.040

- `eq_0013` eq: (none)  [annotated linear]

### 10.1016_j.cie.2019.04.031

- `eq_0039` eq: (none)  [annotated linear]

### 10.1016_j.ejor.2017.04.057

- `eq_0004` eq: partition_row  [annotated linear]
- `eq_0005` ge: difference_row  [annotated linear]
- `eq_0010` le: bound_row  [annotated linear]

### 10.1016_j.ejor.2021.04.041

- **set_partitioning_problem** on `lambda`: eq_0004
- `eq_0004` eq: partition_row  [annotated linear]

### 10.1016_j.jrtpm.2019.100175

- `eq_0005_lo` ge: (none)  [annotated linear]
- `eq_0005_up` le: bound_row  [annotated linear]
- `eq_0011_lo` ge: (none)  [annotated linear]
- `eq_0011_up` le: bound_row  [annotated linear]
- `eq_0019_c_1` eq: (none)  [annotated linear]
- `eq_0019_c_2` eq: (none)  [annotated linear]

### 10.1016_j.trb.2009.07.007

- `eq_0008` eq: partition_row  [annotated linear]

### 10.1016_j.trb.2013.10.013

- `eq_0010` ge: linking_row  [annotated linear]
- `eq_0011` le: bound_row  [annotated linear]
- `eq_0013` eq: partition_row  [annotated linear]

### 10.1016_j.trb.2015.04.001

- `eq_0027` le: packing_row  [annotated linear]

### 10.1016_j.trb.2017.05.008

- **disjunctive_scheduling** (ordering rows only) on `mu`: eq_0025
- `eq_0015` eq: partition_row  [annotated linear]
- `eq_0017` eq: bound_row  [annotated linear]
- `eq_0018` ge: big_m_row  [annotated linear]
- `eq_0025` ge: big_m_row  [annotated linear]

### 10.1016_j.trb.2017.10.002

- `eq_0011` eq: partition_row  [annotated linear]
- `eq_0015` eq: big_m_row  [annotated linear]

### 10.1016_j.trb.2021.04.014

- `eq_0001` eq: (none)  [annotated linear]
- `eq_0003_a` le: difference_row  [annotated linear]
- `eq_0003_b` le: difference_row  [annotated linear]
- `eq_0004` eq: partition_row  [annotated linear]
- `eq_0005` le: linking_row  [annotated linear]
- `eq_0006` le: linking_row  [annotated linear]
- `eq_0009` le: linking_row  [annotated linear]
- `eq_0012` eq: (none)  [annotated capacity]
- `eq_0013` eq: (none)  [annotated capacity]
- `eq_0014_a` ge: (none)  [annotated linear]
- `eq_0014_b` le: bound_row  [annotated linear]
- `eq_0020` ge: difference_row  [annotated linear]

### 10.1016_j.trb.2021.11.009

- `eq_0005` le: packing_row  [annotated linear]

### 10.1016_j.trc.2014.06.003

- `eq_0007` eq: bound_row  [annotated linear]
- `eq_0008_a` ge: (none)  [annotated linear]
- `eq_0008_b` le: bound_row  [annotated linear]
- `eq_0022` le: bound_row  [annotated capacity]
- `eq_0026` le: packing_row  [annotated ordering]

### 10.1016_j.trc.2016.05.005

- `eq_0017_lo` ge: bound_row  [annotated linear]
- `eq_0017_up` le: bound_row  [annotated linear]
- `eq_0018_lo` ge: (none)  [annotated linear]
- `eq_0018_up` le: difference_row  [annotated linear]

### 10.1016_j.trc.2016.11.007

- `eq_0004` eq: (none)  [annotated linear]
- `eq_0011` ge: difference_row  [annotated linear]
- `eq_0012` ge: difference_row  [annotated linear]

### 10.1016_j.trc.2024.104893

- `eq_0096_a` eq: (none)  [annotated linear]
- `eq_0096_b` eq: (none)  [annotated linear]
- `eq_0116_b` ge: difference_row  [annotated linear]
- `eq_0116_c` ge: (none)  [annotated linear]
- `eq_0116_d` ge: difference_row  [annotated linear]
- `eq_0116_e` ge: (none)  [annotated linear]
- `eq_0117_b` ge: (none)  [annotated linear]
- `eq_0117_c` ge: linking_row  [annotated linear]

### 10.1016_j.trc.2025.105441

- `eq_0003` eq: (none)  [annotated linear]
- `eq_0010` eq: (none)  [annotated linear]
- `eq_0015` eq: (none)  [annotated linear]
- `eq_0016_lo` ge: (none)  [annotated linear]
- `eq_0016_up` le: bound_row  [annotated linear]
- `eq_0020` eq: (none)  [annotated linear]
- `eq_0023` le: cardinality_row  [annotated linear]
- `eq_0029_a` le: linking_row  [annotated linear]
- `eq_0029_d_lo` ge: (none)  [annotated linear]
- `eq_0029_d_up` le: bound_row  [annotated linear]
- `eq_0030_a` le: big_m_row, linking_row  [annotated linear]
- `eq_0032_a` eq: (none)  [annotated linear]
- `eq_0032_c` le: difference_row  [annotated linear]
- `eq_0034_b` le: difference_row  [annotated linear]
- `eq_0043_c` eq: (none)  [annotated linear]
- `eq_0043_e` eq: (none)  [annotated linear]
- `eq_0043_f_lo` ge: (none)  [annotated linear]
- `eq_0043_f_up` le: bound_row  [annotated linear]
- `eq_0043_i` eq: (none)  [annotated linear]

### 10.1016_j.tre.2022.102942

- `eq_0001` eq: (none)  [annotated linear]
- `eq_0005` eq: (none)  [annotated linear]
- `eq_0008` eq: (none)  [annotated linear]
- `eq_0010_a` ge: bound_row  [annotated linear]
- `eq_0010_b` le: bound_row  [annotated linear]
- `eq_0016` eq: (none)  [annotated linear]
- `eq_0019` le: difference_row  [annotated linear]
- `eq_0027` eq: (none)  [annotated linear]
- `eq_0032_b` eq: (none)  [annotated linear]
- `eq_0035` eq: (none)  [annotated linear]
- `eq_0042_lo` ge: bound_row  [annotated linear]
- `eq_0042_up` le: bound_row  [annotated linear]
- `eq_0043_lo` ge: bound_row  [annotated linear]
- `eq_0043_up` le: bound_row  [annotated linear]

### GEN:cie.2025.111794

- `delay_def` ge: difference_row  [annotated linear]
- `arrival_lower` ge: (none)  [annotated linear]
- `arrival_upper` le: (none)  [annotated linear]
- `assignment_balance_low` ge: (none)  [annotated linear]
- `assignment_balance_up` le: (none)  [annotated linear]
- `train_feasibility` ge: big_m_row  [annotated linear]

### GEN:ijdrr.2022.103361

- `dep_orig` ge: bound_row  [annotated linear]
- `arr_travel` ge: difference_row  [annotated linear]
- `route_assign` eq: cardinality_row  [annotated linear]
- `cap` le: cardinality_row  [annotated linear]
- `use_rec` le: linking_row  [annotated linear]
- `out_rec` eq: partition_row  [annotated linear]

### GEN:jrtpm.2013.10.001

- **generalized_assignment** (unit weights (capacitated assignment)) on `x`: assign, capacity
- **set_partitioning_problem** on `x`: assign
- `assign` eq: partition_row  [annotated linear]
- `capacity` le: cardinality_row  [annotated linear]

### GEN:jrtpm.2018.02.002

- `len_lower` ge: bound_row  [annotated linear]
- `len_upper` le: (none)  [annotated linear]
- `station_capacity_con` le: knapsack_row  [annotated linear]
- `arc_capacity` le: (none)  [annotated linear]
- `demand_satisfaction` eq: (none)  [annotated linear]
- `path_feasibility` le: bound_row  [annotated linear]

### GEN:omega.2024.103040

- **disjunctive_scheduling** (ordering rows only) on `move`: flow_link
- **disjunctive_scheduling** (ordering rows only) on `order`: headway_precede
- `flow_link` ge: big_m_row  [annotated big_m]
- `move_choice` eq: partition_row  [annotated linear]
- `headway_precede` ge: big_m_row  [annotated big_m]
- `headway_follow` ge: (none)  [annotated big_m]
- `capacity_constr` le: (none)  [annotated linear]
- `delay_def` ge: difference_row  [annotated linear]

### GEN:trb.2023.102786

- **generalized_assignment** (unit weights (capacitated assignment)) on `x`: assign_flow, capacity
- `assign_flow` eq: partition_row  [annotated flow_balance]
- `capacity` le: cardinality_row  [annotated capacity]
- `timing` eq: (none)  [annotated linear]

### INFORMSJoC__2024.0698__mdvsp-ts-arc-flow-time-expanded

- **set_partitioning_problem** on `x`: trip_cover
- `flow_conservation` eq: flow_conservation_row  [annotated linear]
- `trip_cover` eq: partition_row  [annotated linear]
- `depot_outflow_cap` eq: cardinality_row  [annotated linear]

### INFORMSJoC__2024.0698__mdvsp-ts-set-partitioning-master

- **set_partitioning_problem** (LP relaxation (master)) on `lambda`: trip_cover_partitioning
- `trip_cover_partitioning` eq: partition_row  [annotated linear]

### Yinwenxu-1212__crewScheduling__crew-pairing-set-covering-master

- **set_partitioning_problem** on `x`: flight_cover, ground_duty_cover
- `crew_at_most_one_roster` le: packing_row  [annotated linear]
- `flight_cover` eq: partition_row  [annotated linear]
- `ground_duty_cover` eq: partition_row  [annotated linear]

### assignment

- **assignment_block** (classical (rows sum different shape indices)) on `x`: each_worker_one_job, each_job_one_worker
- **set_partitioning_problem** on `x`: each_worker_one_job, each_job_one_worker
- `each_worker_one_job` eq: partition_row  [annotated set_packing/assignment_covering]
- `each_job_one_worker` eq: partition_row  [annotated set_packing/assignment_covering]

### lintim__openlintim__line-planning-cost-model

- **set_covering_problem** (multi-cover (right side is a demand), integer) on `f`: edge_lower_frequency_bound
- `edge_lower_frequency_bound` ge: cardinality_row  [annotated linear]
- `edge_upper_frequency_bound` le: cardinality_row  [annotated linear]

### lintim__openlintim__vehicle-scheduling-ip

- **assignment_block** (degree pair (in/out rows on arcs, same index sets)) on `v`: incoming_degree, outgoing_degree
- **set_partitioning_problem** on `v`: incoming_degree, outgoing_degree
- `incoming_degree` eq: partition_row  [annotated linear]
- `outgoing_degree` eq: partition_row  [annotated linear]

### lp_1_1_fixed_sequence

- `headway` ge: difference_row  [annotated headway]
- `earliest_departure` ge: bound_row  [annotated linear]

### lp_1_5_soft_regularity

- `regularity_soft` eq: (none)  [annotated soft]

### mip_2_1_big_m

- **disjunctive_scheduling** (with pair row) on `y`: order_a, order_pair
- `order_a` ge: big_m_row  [annotated big_m]
- `order_pair` eq: partition_row  [annotated linear]

### mip_2_4_time_indexed

- **generalized_assignment** (unit weights (capacitated assignment)) on `x`: exactly_one_slot, slot_capacity
- **set_partitioning_problem** on `x`: exactly_one_slot
- `exactly_one_slot` eq: partition_row  [annotated set_packing]
- `slot_capacity` le: cardinality_row  [annotated capacity]

### mip_2_8_pesp

- **pesp** on `k,t`: pesp_lower, pesp_upper
- `pesp_lower` ge: pesp_row  [annotated modulo]
- `pesp_upper` le: pesp_row  [annotated modulo]

### multi_obj_lateness_energy

- `must_run` ge: linking_row  [annotated linear]

### objective_abs_deviation


### objective_lex_priority


### pesp_solvable

- **pesp** on `k,t`: pesp_lower, pesp_upper
- `pesp_lower` ge: pesp_row  [annotated modulo/periodic_modulo_pesp]
- `pesp_upper` le: pesp_row  [annotated modulo/periodic_modulo_pesp]

### romain-montagne__vrpy__vrp-set-covering-master

- **set_covering_problem** (multi-cover (right side is a demand)) on `lambda`: set_covering_visit_node
- `set_covering_visit_node` ge: cardinality_row  [annotated linear]
- `vehicle_count_bound` le: cardinality_row  [annotated linear]
- `makespan_definition` le: linking_row  [annotated linear]

