from copy import deepcopy
import torch
import numpy as np
import numpy.random as rd
from AgentNet import QNet, QNetTwin, QNetTwinDuel
from AgentNet import Actor, ActorSAC, ActorPPO
from AgentNet import Critic, CriticAdv, CriticTwin


class AgentDQN:
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        self.explore_rate = 0.1
        self.action_dim = action_dim

        self.obj_a = 0.0
        self.obj_c = (-np.log(0.5)) ** 0.5
        self.state = self.action = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.act = QNet(net_dim, state_dim, action_dim).to(self.device)
        self.act_target = deepcopy(self.act)

        self.criterion = torch.torch.nn.MSELoss()
        self.optimizer = torch.optim.Adam(self.act.parameters(), lr=learning_rate)

    def select_actions(self, states):  # for discrete action space
        if rd.rand() < self.explore_rate:
            a_int = rd.randint(self.action_dim, size=(len(states),))
        else:
            states = torch.as_tensor(states, dtype=torch.float32, device=self.device)
            actions = self.act(states)
            a_int = actions.argmax(dim=1).detach().cpu().numpy()
        return a_int

    def update_buffer(self, env, buffer, max_step, reward_scale, gamma):
        for _ in range(max_step):
            action = self.select_actions((self.state,))[0]
            next_s, reward, done, _ = env.step(action)
            other = (reward * reward_scale, 0.0 if done else gamma, action)
            buffer.append_memo(self.state, other)
            self.state = env.reset() if done else next_s
        return max_step

    def update_policy(self, buffer, max_step, batch_size, repeat_times):
        buffer.update__now_len__before_sample()
        next_q = obj_critic = None
        for _ in range(int(max_step * repeat_times)):
            with torch.no_grad():
                reward, mask, action, state, next_s = buffer.random_sample(batch_size)
                next_q = self.act_target(next_s).max(dim=1, keepdim=True)[0]
                q_label = reward + mask * next_q
            q_eval = self.act(state).gather(1, action.type(torch.long))
            obj_critic = self.criterion(q_eval, q_label)

            self.optimizer.zero_grad()
            obj_critic.backward()
            self.optimizer.step()
            soft_target_update(self.act_target, self.act)
        self.obj_a = next_q.mean().item()
        self.obj_c = obj_critic.item()


class AgentDoubleDQN(AgentDQN):
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        super().__init__(net_dim, state_dim, action_dim, learning_rate)
        self.explore_rate = 0.25  # epsilon-greedy, the rate of choosing random action
        self.softmax = torch.nn.Softmax(dim=1)
        self.action_dim = action_dim

        self.act = QNetTwin(net_dim, state_dim, action_dim).to(self.device)
        self.act_target = deepcopy(self.act)

        self.criterion = torch.nn.MSELoss()
        self.optimizer = torch.optim.Adam(self.act.parameters(), lr=learning_rate)

    def select_actions(self, states):  # for discrete action space
        states = torch.as_tensor(states, dtype=torch.float32, device=self.device)
        actions = self.act(states)
        if rd.rand() < self.explore_rate:
            a_prob_l = self.softmax(actions).detach().cpu().numpy()
            a_int = [rd.choice(self.action_dim, p=a_prob) for a_prob in a_prob_l]
        else:
            a_int = actions.argmax(dim=1).detach().cpu().numpy()
        return a_int

    def update_policy(self, buffer, max_step, batch_size, repeat_times):
        buffer.update__now_len__before_sample()

        next_q = obj_critic = None
        for _ in range(int(max_step * repeat_times)):
            with torch.no_grad():
                reward, mask, action, state, next_s = buffer.random_sample(batch_size)
                next_q = self.act_target(next_s).max(dim=1, keepdim=True)[0]
                q_label = reward + mask * next_q
            action = action.type(torch.long)
            q_eval1, q_eval2 = [qs.gather(1, action) for qs in self.act.get__q1_q2(state)]
            obj_critic = self.criterion(q_eval1, q_label) + self.criterion(q_eval2, q_label)

            self.optimizer.zero_grad()
            obj_critic.backward()
            self.optimizer.step()
            soft_target_update(self.act_target, self.act)
        self.obj_a = next_q.mean().item()
        self.obj_c = obj_critic.item() / 2


class AgentD3QN(AgentDoubleDQN):  # Dueling Double DQN
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        super().__init__(net_dim, state_dim, action_dim, learning_rate)
        self.explore_rate = 0.25  # epsilon-greedy, the rate of choosing random action

        self.act = QNetTwinDuel(net_dim, state_dim, action_dim).to(self.device)
        self.act_target = deepcopy(self.act)

        self.criterion = torch.nn.SmoothL1Loss()
        self.optimizer = torch.optim.Adam(self.act.parameters(), lr=learning_rate)


class AgentBase:
    def __init__(self):
        self.obj_a = 0.0
        self.obj_c = (-np.log(0.5)) ** 0.5
        self.state = None
        self.action = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.act = None
        self.cri = None
        self.criterion = None
        self.optimizer = None

    def select_actions(self, states):  # states = (state, ...)
        return (None,)  # -1 < action < +1

    def update_buffer(self, env, buffer, max_step, reward_scale, gamma):
        for _ in range(max_step):
            action = self.select_actions((self.state,))[0]
            next_s, reward, done, _ = env.step(action)
            other = (reward * reward_scale, 0.0 if done else gamma, *action)
            buffer.append_memo(self.state, other)
            self.state = env.reset() if done else next_s
        return max_step


class AgentDDPG(AgentBase):
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        super().__init__()
        self.explore_noise = 0.05

        self.act = Actor(net_dim, state_dim, action_dim).to(self.device)
        self.act_target = deepcopy(self.act)
        self.cri = Critic(net_dim, state_dim, action_dim).to(self.device)
        self.cri_target = deepcopy(self.cri)

        self.criterion = torch.nn.MSELoss()
        self.optimizer = torch.optim.Adam([{'params': self.act.parameters(), 'lr': learning_rate},
                                           {'params': self.cri.parameters(), 'lr': learning_rate}])

    def select_actions(self, states):  # states = (state, ...)
        states = torch.as_tensor(states, dtype=torch.float32, device=self.device)
        actions = self.act(states)
        actions = (actions + torch.randn_like(actions) * self.explore_noise).clamp(-1, 1)
        return actions.detach().cpu().numpy()

    def update_policy(self, buffer, max_step, batch_size, repeat_times):
        buffer.update__now_len__before_sample()
        obj_critic = obj_actor = None  # just for print return
        for _ in range(int(max_step * repeat_times)):
            with torch.no_grad():
                reward, mask, action, state, next_s = buffer.random_sample(batch_size)
                next_q = self.cri_target(next_s, self.act_target(next_s))
                q_label = reward + mask * next_q
            q_value = self.cri(state, action)
            obj_critic = self.criterion(q_value, q_label)

            q_value_pg = self.act(state)  # policy gradient
            obj_actor = -self.cri_target(state, q_value_pg).mean()

            obj_united = obj_actor + obj_critic  # objective
            self.optimizer.zero_grad()
            obj_united.backward()
            self.optimizer.step()

            soft_target_update(self.cri_target, self.cri)
            soft_target_update(self.act_target, self.act)
        self.obj_a = obj_actor.item()
        self.obj_c = obj_critic.item()


class AgentTD3(AgentDDPG):
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        super().__init__(net_dim, state_dim, action_dim, learning_rate)
        self.explore_noise = 0.1  # standard deviation of explore noise
        self.policy_noise = 0.2  # standard deviation of policy noise
        self.update_freq = 2  # delay update frequency, for soft target update

        self.cri = CriticTwin(net_dim, state_dim, action_dim).to(self.device)
        self.cri_target = deepcopy(self.cri)

        self.optimizer = torch.optim.Adam([{'params': self.act.parameters(), 'lr': learning_rate},
                                           {'params': self.cri.parameters(), 'lr': learning_rate}])

    def update_policy(self, buffer, max_step, batch_size, repeat_times):
        buffer.update__now_len__before_sample()

        obj_critic = obj_actor = None
        for i in range(int(max_step * repeat_times)):
            with torch.no_grad():
                reward, mask, action, state, next_s = buffer.random_sample(batch_size)
                next_a = self.act_target.get_action(next_s, self.policy_noise)  # policy noise
                next_q = torch.min(*self.cri_target.get__q1_q2(next_s, next_a))  # twin critics
                q_label = reward + mask * next_q
            q1, q2 = self.cri.get__q1_q2(state, action)
            obj_critic = self.criterion(q1, q_label) + self.criterion(q2, q_label)  # twin critics

            q_value_pg = self.act(state)  # policy gradient
            obj_actor = -self.cri_target(state, q_value_pg).mean()

            obj_united = obj_actor + obj_critic  # objective
            self.optimizer.zero_grad()
            obj_united.backward()
            self.optimizer.step()
            if i % self.update_freq == 0:  # delay update
                soft_target_update(self.cri_target, self.cri)
                soft_target_update(self.act_target, self.act)
        self.obj_a = obj_actor.item()
        self.obj_c = obj_critic.item()


class AgentPPO(AgentBase):
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        super().__init__()
        self.clip = 0.25  # ratio.clamp(1 - clip, 1 + clip)
        self.lambda_entropy = 0.01  # could be 0.02

        self.act = ActorPPO(net_dim, state_dim, action_dim).to(self.device)
        self.cri = CriticAdv(state_dim, net_dim).to(self.device)

        self.criterion = torch.nn.SmoothL1Loss()
        self.optimizer = torch.optim.Adam([{'params': self.act.parameters(), 'lr': learning_rate},
                                           {'params': self.cri.parameters(), 'lr': learning_rate}])

    def select_actions(self, states):  # states = (state, ...)
        states = torch.as_tensor(states, dtype=torch.float32, device=self.device)
        a_noise, noise = self.act.get__action_noise(states)
        return a_noise.detach().cpu().numpy(), noise.detach().cpu().numpy()

    def update_buffer(self, env, buffer, max_step, reward_scale, gamma):
        buffer.empty_memories__before_explore()

        step_counter = 0
        target_step = buffer.max_len - max_step
        while step_counter < target_step:
            state = env.reset()
            for _ in range(max_step):
                action, noise = self.select_actions((state,))
                action = action[0]
                noise = noise[0]

                next_state, reward, done, _ = env.step(np.tanh(action))
                step_counter += 1

                other = (reward * reward_scale, 0.0 if done else gamma, *action, *noise)
                buffer.append_memo(state, other)
                if done:
                    break
                state = next_state
        return step_counter

    def update_policy(self, buffer, _max_step, batch_size, repeat_times=8):
        buffer.update__now_len__before_sample()
        max_memo = buffer.now_len

        '''Trajectory using reverse reward'''
        with torch.no_grad():
            all_reward, all_mask, all_action, all_noise, all_state = buffer.sample_for_ppo()

            b_size = 2 ** 10
            all__new_v = [self.cri(all_state[i:i + b_size]) for i in range(0, all_state.size(0), b_size)]
            all__new_v = torch.cat(all__new_v, dim=0)
            all_log_prob = -(all_noise.pow(2).__mul__(0.5) + self.act.a_std_log + self.act.sqrt_2pi_log).sum(1)

            '''get all__adv_v'''
            all__old_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # old policy value
            prev_old_v = 0  # old q value
            for i in range(max_memo - 1, -1, -1):  # could be more elegant
                all__old_v[i] = all_reward[i] + all_mask[i] * prev_old_v
                prev_old_v = all__old_v[i]

            all__adv_v = all__old_v - (all_mask * all__new_v).squeeze(1)
            all__adv_v = all__adv_v / (all__adv_v.std() + 1e-5)

            del all_reward, all_mask, all_noise

        '''PPO: Surrogate objective of Trust Region'''
        obj_actor = obj_critic = None
        for _ in range(int(repeat_times * max_memo / batch_size)):
            indices = torch.randint(max_memo, size=(batch_size,), requires_grad=False, device=self.device)
            state = all_state[indices]
            action = all_action[indices]
            advantage = all__adv_v[indices]
            old_value = all__old_v[indices]
            old_log_prob = all_log_prob[indices]

            new_log_prob = self.act.compute__log_prob(state, action)  # it is obj_actor
            ratio = (new_log_prob - old_log_prob).exp()
            obj_surrogate1 = advantage * ratio
            obj_surrogate2 = advantage * ratio.clamp(1 - self.clip, 1 + self.clip)
            obj_actor = -torch.min(obj_surrogate1, obj_surrogate2).mean()

            new_value = self.cri(state).squeeze(1)
            obj_critic = self.criterion(new_value, old_value)

            obj_united = obj_actor + obj_critic / (old_value.std() + 1e-5)
            self.optimizer.zero_grad()
            obj_united.backward()
            self.optimizer.step()
        self.obj_a = obj_actor.item()
        self.obj_c = obj_critic.item()


class AgentGaePPO(AgentPPO):
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        super().__init__(net_dim, state_dim, action_dim, learning_rate)
        self.clip = 0.25  # ratio.clamp(1 - clip, 1 + clip)
        self.lambda_adv = 0.98  # could be 0.95~0.99
        self.lambda_entropy = 0.01  # could be 0.02

    def update_policy(self, buffer, _max_step, batch_size, repeat_times=8):
        buffer.update__now_len__before_sample()
        max_memo = buffer.now_len

        '''Trajectory using Generalized Advantage Estimation (GAE)'''
        with torch.no_grad():
            all_reward, all_mask, all_action, all_noise, all_state = buffer.sample_for_ppo()

            b_size = 2 ** 10
            all__new_v = [self.cri(all_state[i:i + b_size]) for i in range(0, all_state.size(0), b_size)]
            all__new_v = torch.cat(all__new_v, dim=0)
            all_log_prob = -(all_noise.pow(2).__mul__(0.5) + self.act.a_std_log + self.act.sqrt_2pi_log).sum(1)

            '''get all__adv_v'''
            all__old_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # old policy value
            all__adv_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # advantage value

            prev_old_v = 0  # old q value
            prev_gae_v = 0  # GAE q value
            for i in range(max_memo - 1, -1, -1):  # could be more elegant
                all__old_v[i] = all_reward[i] + all_mask[i] * prev_old_v
                prev_old_v = all__old_v[i]
                all__adv_v[i] = all_reward[i] + all_mask[i] * prev_gae_v - all__new_v[i]
                prev_gae_v = all__new_v[i] + all__adv_v[i] * self.lambda_adv
            all__adv_v = all__adv_v / (all__adv_v.std() + 1e-5)

            del all_reward, all_mask, all_noise

        '''PPO: Clipped Surrogate objective of Trust Region'''
        obj_actor = obj_critic = None
        for _ in range(int(repeat_times * max_memo / batch_size)):
            indices = torch.randint(max_memo, size=(batch_size,), device=self.device)

            state = all_state[indices]
            action = all_action[indices]
            advantage = all__adv_v[indices]
            old_value = all__old_v[indices]
            old_log_prob = all_log_prob[indices]

            new_log_prob = self.act.compute__log_prob(state, action)
            ratio = (new_log_prob - old_log_prob).exp()
            obj_surrogate1 = advantage * ratio
            obj_surrogate2 = advantage * ratio.clamp(1 - self.clip, 1 + self.clip)
            obj_surrogate = -torch.min(obj_surrogate1, obj_surrogate2).mean()
            obj_entropy = (new_log_prob.exp() * new_log_prob).mean() * self.lambda_entropy  # policy entropy
            obj_actor = obj_surrogate + obj_entropy

            new_value = self.cri(state).squeeze(1)
            obj_critic = self.criterion(new_value, old_value)

            obj_united = obj_actor + obj_critic / (old_value.std() + 1e-5)
            self.optimizer.zero_grad()
            obj_united.backward()
            self.optimizer.step()

        self.obj_a = obj_actor.item()
        self.obj_c = obj_critic.item()


class AgentSAC(AgentBase):
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        super().__init__()
        self.target_entropy = np.log(action_dim)
        self.alpha_log = torch.tensor((-np.log(action_dim) * np.e,), dtype=torch.float32,
                                      requires_grad=True, device=self.device)

        self.act = ActorSAC(net_dim, state_dim, action_dim).to(self.device)
        self.act_target = deepcopy(self.act)
        self.cri = CriticTwin(net_dim, state_dim, action_dim, ).to(self.device)
        self.cri_target = deepcopy(self.cri)

        self.criterion = torch.nn.MSELoss()
        self.optimizer = torch.optim.Adam([{'params': self.act.parameters(), 'lr': learning_rate},
                                           {'params': self.cri.parameters(), 'lr': learning_rate},
                                           {'params': (self.alpha_log,), 'lr': learning_rate}])

    def select_actions(self, states):  # states = (state, ...)
        states = torch.as_tensor(states, dtype=torch.float32, device=self.device)
        actions = self.act.get_action(states)
        return actions.detach().cpu().numpy()

    def update_policy(self, buffer, max_step, batch_size, repeat_times):
        buffer.update__now_len__before_sample()
        alpha = self.alpha_log.exp().detach()
        obj_actor = obj_critic = None
        for _ in range(int(max_step * repeat_times)):
            with torch.no_grad():
                reward, mask, action, state, next_s = buffer.random_sample(batch_size)
                next_a, next_log_prob = self.act_target.get__action__log_prob(next_s)
                next_q = torch.min(*self.cri_target.get__q1_q2(next_s, next_a))
                q_label = reward + mask * (next_q + next_log_prob * alpha)
            q1, q2 = self.cri.get__q1_q2(state, action)
            obj_critic = self.criterion(q1, q_label) + self.criterion(q2, q_label)

            action_pg, log_prob = self.act.get__action__log_prob(state)  # policy gradient
            obj_alpha = (self.alpha_log * (log_prob - self.target_entropy).detach()).mean()

            alpha = self.alpha_log.exp().detach()
            obj_actor = -(torch.min(*self.cri_target.get__q1_q2(state, action_pg)) + log_prob * alpha).mean()

            obj_united = obj_critic + obj_alpha + obj_actor
            self.optimizer.zero_grad()
            obj_united.backward()
            self.optimizer.step()
            soft_target_update(self.cri_target, self.cri)
            soft_target_update(self.act_target, self.act)
        self.obj_a = obj_actor.item()
        self.obj_c = obj_critic.item()


class AgentModSAC(AgentSAC):  # Modify SAC
    def __init__(self, net_dim, state_dim, action_dim, learning_rate=1e-4):
        super().__init__(net_dim, state_dim, action_dim, learning_rate)
        self.criterion = torch.nn.SmoothL1Loss()

    def update_policy(self, buffer, max_step, batch_size, repeat_times):
        buffer.update__now_len__before_sample()

        k = 1.0 + buffer.now_len / buffer.max_len
        batch_size_ = int(batch_size * k)
        train_steps = int(max_step * k * repeat_times)

        alpha = self.alpha_log.exp().detach()
        update_a = 0
        for update_c in range(1, train_steps):
            with torch.no_grad():
                reward, mask, action, state, next_s = buffer.random_sample(batch_size_)
                next_a, next_log_prob = self.act_target.get__action__log_prob(next_s)
                next_q = torch.min(*self.cri_target.get__q1_q2(next_s, next_a))
                q_label = reward + mask * (next_q + next_log_prob * alpha)
            q1, q2 = self.cri.get__q1_q2(state, action)
            obj_critic = self.criterion(q1, q_label) + self.criterion(q2, q_label)
            self.obj_c = 0.995 * self.obj_c + 0.0025 * obj_critic.item()

            a_noise_pg, log_prob = self.act.get__action__log_prob(state)  # policy gradient
            obj_alpha = (self.alpha_log * (log_prob - self.target_entropy).detach()).mean()

            lamb = np.exp(-self.obj_c ** 2)
            if_update_a = update_a / update_c < 1 / (2 - lamb)
            if if_update_a:  # auto TTUR
                update_a += 1

                with torch.no_grad():
                    self.alpha_log[:] = self.alpha_log.clamp(-16, 2)
                alpha = self.alpha_log.exp().detach()
                obj_actor = -(torch.min(*self.cri_target.get__q1_q2(state, a_noise_pg)) + log_prob * alpha).mean()
                self.obj_a = 0.995 * self.obj_a + 0.005 * q_label.mean().item()

                obj_united = obj_critic + obj_alpha + obj_actor
            else:
                obj_united = obj_critic + obj_alpha

            self.optimizer.zero_grad()
            obj_united.backward()
            self.optimizer.step()
            soft_target_update(self.cri_target, self.cri)
            soft_target_update(self.act_target, self.act) if if_update_a else None


def soft_target_update(target, current, tau=5e-3):
    for target_param, param in zip(target.parameters(), current.parameters()):
        target_param.data.copy_(tau * param.data + (1.0 - tau) * target_param.data)
