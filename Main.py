import os
import time
import torch
import numpy as np
import numpy.random as rd


class Arguments:
    def __init__(self, agent_rl=None, env=None, gpu_id=None, if_on_policy=False):
        self.agent_rl = agent_rl  # Deep Reinforcement Learning algorithm
        self.gpu_id = gpu_id  # choose the GPU for running. gpu_id is None means set it automatically
        self.cwd = None  # current work directory. cwd is None means set it automatically
        self.env = env  # the environment for training

        '''Arguments for off-policy training'''
        self.net_dim = 2 ** 8  # the network width
        self.batch_size = 2 ** 7  # num of transitions sampled from replay buffer.
        self.repeat_times = 2 ** 0  # repeatedly update network to keep critic's loss small
        self.max_memo = 2 ** 17  # capacity of replay buffer
        if if_on_policy:
            self.net_dim = 2 ** 9
            self.batch_size = 2 ** 8
            self.repeat_times = 2 ** 4
            self.max_memo = 2 ** 12
        self.max_step = 2 ** 10  # max steps in one training episode
        self.reward_scale = 2 ** 0  # an approximate target reward usually be closed to 256
        self.gamma = 0.99  # discount factor of future rewards
        self.rollout_num = 2  # the number of rollout workers (larger is not always faster)
        self.num_threads = 4  # cpu_num for evaluate model, torch.set_num_threads(self.num_threads)

        '''Arguments for evaluate'''
        self.if_remove = True  # remove the cwd folder? (True, False, None:ask me)
        self.if_break_early = True  # break training after 'eval_reward > target reward'
        self.break_step = 2 ** 20  # break training after 'total_step > break_step'
        self.eval_times = 2 ** 3  # evaluation times if 'eval_reward > target_reward'
        self.show_gap = 2 ** 8  # show the Reward and Loss value per show_gap seconds
        self.random_seed = 0  # initialize random seed in self.init_before_training(

    def init_before_training(self):
        import sys
        self.gpu_id = sys.argv[-1][-4] if self.gpu_id is None else str(self.gpu_id)
        self.cwd = f'./{self.agent_rl.__name__}/{self.env.env_name}_{self.gpu_id}' if self.cwd is None else self.cwd
        print(f'| GPU id: {self.gpu_id}, cwd: {self.cwd}')

        import shutil  # remove history according to bool(if_remove)
        if self.if_remove is None:
            self.if_remove = bool(input("PRESS 'y' to REMOVE: {}? ".format(self.cwd)) == 'y')
        if self.if_remove:
            shutil.rmtree(self.cwd, ignore_errors=True)
            print("| Remove history")
        os.makedirs(self.cwd, exist_ok=True)

        os.environ['CUDA_VISIBLE_DEVICES'] = str(self.gpu_id)
        torch.set_num_threads(self.num_threads)
        torch.set_default_dtype(torch.float32)
        torch.manual_seed(self.random_seed)
        np.random.seed(self.random_seed)


def run__demo():
    import gym
    import Agent
    from Env import decorate_env
    args = Arguments(agent_rl=None, env=None, gpu_id=None)  # see Arguments() to see hyper-parameters

    '''DEMO 1: Discrete action env: CartPole-v0 of gym'''
    args.env = decorate_env(env=gym.make('CartPole-v0'))
    args.agent_rl = Agent.AgentDoubleDQN  # you can also change RL algorithm in this way
    args.net_dim = 2 ** 7  # change a default hyper-parameters
    train_and_evaluate(args)
    exit()

    '''DEMO 2: Continuous action env: gym.box2D'''
    # args.env = decorate_env(env=gym.make('Pendulum-v0'))
    args.env = decorate_env(env=gym.make('LunarLanderContinuous-v2'))
    # args.env = decorate_env(env=gym.make('BipedalWalker-v3'))  # recommend args.gamma = 0.95
    args.agent_rl = Agent.AgentSAC  # off-policy
    train_and_evaluate(args)
    exit()

    # args = Arguments(if_on_policy=True)  # on-policy has different hyper-parameters from off-policy
    # # args.env = decorate_env(env=gym.make('Pendulum-v0'))
    # args.env = decorate_env(env=gym.make('LunarLanderContinuous-v2'))
    # # args.env = decorate_env(env=gym.make('BipedalWalker-v3'))  # recommend args.gamma = 0.95
    # args.agent_rl = Agent.AgentPPO  # on-policy
    # train_and_evaluate(args)
    # exit()

    '''DEMO 3: Custom Continuous action env: FinanceStock-v1'''
    from Env import FinanceMultiStockEnv
    args = Arguments(if_on_policy=True)
    args.env = FinanceMultiStockEnv()  # a standard env for ElegantRL, not need decorate_env()
    args.agent_rl = Agent.AgentPPO  # PPO+GAE (on-policy)

    args.break_step = int(5e6 * 4)  # 5e6 (15e6) UsedTime 3,000s (9,000s)
    args.net_dim = 2 ** 8
    args.max_step = 1699
    args.max_memo = (args.max_step - 1) * 16
    args.batch_size = 2 ** 11
    args.repeat_times = 2 ** 4
    train_and_evaluate(args)
    exit()


def train_and_evaluate(args):
    args.init_before_training()

    agent_rl = args.agent_rl  # basic arguments
    agent_id = args.gpu_id
    env = args.env
    cwd = args.cwd

    gamma = args.gamma  # training arguments
    net_dim = args.net_dim
    max_memo = args.max_memo
    max_step = args.max_step
    batch_size = args.batch_size
    repeat_times = args.repeat_times
    reward_scale = args.reward_scale

    show_gap = args.show_gap  # evaluate arguments
    eval_times = args.eval_times
    break_step = args.break_step
    if_break_early = args.if_break_early
    del args  # In order to show these hyper-parameters clearly, I put them above.

    '''init: env'''
    state_dim = env.state_dim
    action_dim = env.action_dim
    if_discrete = env.if_discrete
    target_reward = env.target_reward
    from copy import deepcopy  # built-in library of Python
    env_eval = deepcopy(env)
    del deepcopy

    evaluator = Evaluator(cwd, agent_id, eval_times, show_gap, target_reward)  # build Evaluator
    agent = agent_rl(net_dim, state_dim, action_dim)  # build AgentRL
    agent.state = env.reset()

    if_on_policy = agent_rl.__name__ in {'AgentPPO', 'AgentGaePPO'}  # build ReplayBuffer
    if if_on_policy:
        buffer = ReplayBufferCPU(max_memo, state_dim, action_dim=1 if if_discrete else action_dim)
        steps = 0
    else:
        buffer = ReplayBufferGPU(max_memo, state_dim, action_dim=1 if if_discrete else action_dim)
        with torch.no_grad():  # update replay buffer
            steps = explore_before_train(env, buffer, max_step, reward_scale, gamma)
        agent.update_policy(buffer, max_step, batch_size, repeat_times)  # pre-training and hard update
        agent.act_target.load_state_dict(agent.act.state_dict()) if 'act_target' in dir(agent) else None
    total_step = steps

    if_solve = False
    while not ((if_break_early and if_solve) or total_step > break_step or os.path.exists(f'{cwd}/stop')):
        with torch.no_grad():  # speed up running
            steps = agent.update_buffer(env, buffer, max_step, reward_scale, gamma)
        total_step += steps

        buffer.update__now_len__before_sample()
        agent.update_policy(buffer, max_step, batch_size, repeat_times)

        with torch.no_grad():  # speed up running
            evaluator.evaluate_and_save(env_eval, agent.act, agent.device, steps, agent.obj_a, agent.obj_c)


'''utils'''


def explore_before_train(env, buffer, target_step, reward_scale, gamma):
    # just for off-policy. Because on-policy don't explore before training.
    if_discrete = env.if_discrete
    action_dim = env.action_dim

    state = env.reset()
    steps = 0

    while steps < target_step:
        action = rd.randint(action_dim) if if_discrete else rd.uniform(-1, 1, size=action_dim)
        next_state, reward, done, _ = env.step(action)
        steps += 1

        scaled_reward = reward * reward_scale
        mask = 0.0 if done else gamma
        other = (scaled_reward, mask, action) if if_discrete else (scaled_reward, mask, *action)
        buffer.append_memo(state, other)

        state = env.reset() if done else next_state
    return steps


class ReplayBufferBase:
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.max_len = None
        self.now_len = 0
        self.next_idx = 0
        self.if_full = False

        self.all_state = None
        self.all_other = None

    def update__now_len__before_sample(self):
        self.now_len = self.max_len if self.if_full else self.next_idx

    def empty_memories__before_explore(self):
        self.next_idx = 0
        self.now_len = 0
        self.if_full = False


class ReplayBufferCPU(ReplayBufferBase):  # for on-policy
    def __init__(self, max_len, state_dim, action_dim):
        super().__init__()
        self.max_len = max_len
        self.is_full = True
        self.action_dim = action_dim  # for self.sample_for_ppo(

        other_dim = 1 + 1 + action_dim * 2
        self.all_other = np.empty((max_len, other_dim), dtype=np.float32)
        self.all_state = np.empty((max_len, state_dim), dtype=np.float32)

        self.action_dim = action_dim  # for self.sample_for_ppo(

    def append_memo(self, state, other):  # for AgentPPO.update_buffer(
        self.all_state[self.next_idx] = state
        self.all_other[self.next_idx] = other

        self.next_idx += 1
        if self.next_idx >= self.max_len:
            self.is_full = True
            self.next_idx = 0

    def sample_for_ppo(self):
        all_other = torch.as_tensor(self.all_other[:self.now_len], device=self.device)
        return (all_other[:, 0:1],  # reward
                all_other[:, 1:2],  # mask = 0.0 if done else gamma
                all_other[:, 2:2 + self.action_dim],  # action
                all_other[:, 2 + self.action_dim:],  # noise
                torch.as_tensor(self.all_state[:self.now_len], device=self.device))  # state


class ReplayBufferGPU(ReplayBufferBase):
    def __init__(self, max_len, state_dim, action_dim, if_on_policy=False):
        super().__init__()
        self.max_len = max_len
        self.if_full = False

        other_dim = 1 + 1 + action_dim * 2 if if_on_policy else 1 + 1 + action_dim
        self.all_other = torch.empty((max_len, other_dim), dtype=torch.float32, device=self.device)
        self.all_state = torch.empty((max_len, state_dim), dtype=torch.float32, device=self.device)

    def append_memo(self, state, other):
        self.all_state[self.next_idx, :] = torch.as_tensor(state, device=self.device)
        self.all_other[self.next_idx] = torch.as_tensor(other, device=self.device)

        self.next_idx += 1
        if self.next_idx >= self.max_len:
            self.if_full = True
            self.next_idx = 0

    def random_sample(self, batch_size):
        indices = torch.randint(self.now_len - 1, size=(batch_size,), device=self.device)
        r_m_a = self.all_other[indices]
        return (r_m_a[:, 0:1],  # reward
                r_m_a[:, 1:2],  # mask = 0.0 if done else gamma
                r_m_a[:, 2:],  # action
                self.all_state[indices],  # state
                self.all_state[indices + 1])  # next_state


class Evaluator:
    def __init__(self, cwd, agent_id, eval_times, show_gap, target_reward):
        self.recorder = [(0., -np.inf, 0., 0., 0.), ]  # total_step, r_avg, r_std, obj_a, obj_c
        self.r_max = -np.inf
        self.total_step = 0

        self.cwd = cwd  # constant
        self.agent_id = agent_id
        self.show_gap = show_gap
        self.eva_times = eval_times
        self.target_reward = target_reward

        self.used_time = None
        self.start_time = time.time()
        self.print_time = time.time()
        print(f"{'ID':>2}  {'Step':>8}  {'MaxR':>8} |{'avgR':>8}  {'stdR':>8}   {'objA':>8}  {'objC':>8}")

    def evaluate_and_save(self, env, act, device, steps, obj_a, obj_c):
        if_save = False
        reward_list = [get_episode_return(env, act, device) for _ in range(self.eva_times)]

        r_avg = np.average(reward_list)  # episode return average
        if r_avg > self.r_max:  # check final
            self.r_max = r_avg
            if_save = True
        r_std = float(np.std(reward_list))  # episode return std

        self.total_step += steps
        self.recorder.append((self.total_step, r_avg, r_std, obj_a, obj_c))  # update recorder

        if_solve = bool(self.r_max > self.target_reward)  # check if_solve
        if if_solve and self.used_time is None:
            self.used_time = int(time.time() - self.start_time)
            print(f"{'ID':>2}  {'Step':>8}  {'TargetR':>8} |"
                  f"{'avgR':>8}  {'stdR':>8}   {'UsedTime':>8}  ########\n"
                  f"{self.agent_id:<2}  {self.total_step:8.2e}  {self.target_reward:8.2f} |"
                  f"{r_avg:8.2f}  {r_std:8.2f}   {self.used_time:>8}  ########")

        if time.time() - self.print_time > self.show_gap:
            self.print_time = time.time()
            print(f"{self.agent_id:<2}  {self.total_step:8.2e}  {self.r_max:8.2f} |"
                  f"{r_avg:8.2f}  {r_std:8.2f}   {obj_a:8.2f}  {obj_c:8.2f}")

        if if_save:  # save checkpoint with highest episode return
            act_save_path = f'{self.cwd}/actor.pth'
            torch.save(act.state_dict(), act_save_path)
            print(f"{self.agent_id:<2}  {self.total_step:8.2e}  {self.r_max:8.2f} |")
        return if_save


def get_episode_return(env, act, device) -> float:
    episode_return = 0.0  # sum of rewards in an episode
    max_step = env.max_step if hasattr(env, 'max_step') else 2 ** 10
    if_discrete = env.if_discrete

    state = env.reset()
    for _ in range(max_step):
        s_tensor = torch.as_tensor((state,), device=device)
        a_tensor = act(s_tensor)
        if if_discrete:
            a_tensor = a_tensor.argmax(dim=1)
        action = a_tensor.cpu().numpy()[0]  # not need detach(), because with torch.no_grad() outside

        state, reward, done, _ = env.step(action)
        episode_return += reward
        if done:
            break
    return env.episode_return if hasattr(env, 'episode_return') else episode_return


if __name__ == '__main__':
    run__demo()
