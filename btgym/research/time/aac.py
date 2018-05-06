
import numpy as np
import time
import datetime

from btgym.research.gps.aac import GuidedAAC
from btgym.algorithms.runner.synchro import BaseSynchroRunner


class TA3C(GuidedAAC):
    """
    Temporally dependant vanilla A3C. This is mot a meta-learning class.
    Requires stateful temporal data stream provider class such as btgym.datafeed.time.BTgymTimeDataDomain
    """

    def __init__(
            self,
            runner_config=None,
            trial_source_target_cycle=(1, 0),
            num_episodes_per_trial=1,  # one-shot adaptation
            test_slowdown_steps=1,
            episode_sample_params=(1.0, 1.0),
            trial_sample_params=(1.0, 1.0),
            _aux_render_modes=('action_prob', 'value_fn', 'lstm_1_h', 'lstm_2_h'),
            _use_target_policy=False,
            name='TemporalA3C',
            **kwargs
    ):
        try:
            if runner_config is None:
                self.runner_config = {
                    'class_ref': BaseSynchroRunner,
                    'kwargs': {
                        'data_sample_config': {'mode': 0},
                        'test_conditions': {
                            'state': {
                                'metadata': {
                                    'trial_type': 1,  # only test episode from target dom. considered test one
                                    'type': 1
                                }
                            }
                        },
                        'slowdown_steps': test_slowdown_steps,
                        'name': '',
                    },
                }
            else:
                self.runner_config = runner_config

            # Trials sampling control:
            self.num_source_trials = trial_source_target_cycle[0]
            self.num_target_trials = trial_source_target_cycle[-1]
            self.num_episodes_per_trial = num_episodes_per_trial

            self.test_slowdown_steps = test_slowdown_steps

            self.episode_sample_params = episode_sample_params
            self.trial_sample_params = trial_sample_params

            self.global_timestamp = 0

            self.current_source_trial = 0
            self.current_target_trial = 0
            self.current_trial_mode = 0  # source
            self.current_episode = 1

            super(TA3C, self).__init__(
                runner_config=self.runner_config,
                _aux_render_modes=_aux_render_modes,
                name=name,
                **kwargs
            )
        except:
            msg = '{}.__init()__ exception occurred'.format(name) + \
                  '\n\nPress `Ctrl-C` or jupyter:[Kernel]->[Interrupt] for clean exit.\n'
            self.log.exception(msg)
            raise RuntimeError(msg)

    def get_sample_config(self, **kwargs):
        """
        Returns environment configuration parameters for next episode to sample.

        Here we always prescribe to sample test episode from source or target domain.

        Args:
              kwargs:     not used

        Returns:
            configuration dictionary of type `btgym.datafeed.base.EnvResetConfig`
        """

        new_trial = 0
        if self.current_episode >= self.num_episodes_per_trial:
            # Reset episode counter:
            self.current_episode = 0

            # Request new trial:
            new_trial = 1
            # Decide on trial type (source/target):
            if self.current_source_trial >= self.num_source_trials:
                # Time to switch to target mode:
                self.current_trial_mode = 1
                # Reset counters:
                self.current_source_trial = 0
                self.current_target_trial = 0

            if self.current_target_trial >= self.num_target_trials:
                # Vise versa:
                self.current_trial_mode = 0
                self.current_source_trial = 0
                self.current_target_trial = 0

            # Update counter:
            if self.current_trial_mode:
                self.current_target_trial += 1
            else:
                self.current_source_trial += 1

        self.current_episode += 1

        if self.task == 0:
            trial_sample_type = 1

        else:
            trial_sample_type = self.current_trial_mode

        # Compose btgym.datafeed.base.EnvResetConfig-consistent dict:
        sample_config = dict(
            episode_config=dict(
                get_new=True,
                sample_type=1,
                timestamp= self.global_timestamp,
                b_alpha=self.episode_sample_params[0],
                b_beta=self.episode_sample_params[-1]
            ),
            trial_config=dict(
                get_new=new_trial,
                sample_type=trial_sample_type,
                timestamp=self.global_timestamp,
                b_alpha=self.trial_sample_params[0],
                b_beta=self.trial_sample_params[-1]
            )
        )
        return sample_config

    def process(self, sess, **kwargs):
        try:
            sess.run(self.sync_pi)
            # Get data configuration:
            data_config = self.get_sample_config()

            # self.log.warning('data_config: {}'.format(data_config))

            # If this step data comes from source or target domain
            is_test = data_config['trial_config']['sample_type'] and data_config['episode_config']['sample_type']

            # self.log.warning('is_test: {}'.format(is_test))

            if is_test:
                if self.task == 0:
                    self.process_test(sess, data_config)

                else:
                    pass

            else:
                self.process_train(sess, data_config)

        except:
            msg = 'process() exception occurred' + \
                '\n\nPress `Ctrl-C` or jupyter:[Kernel]->[Interrupt] for clean exit.\n'
            self.log.exception(msg)
            raise RuntimeError(msg)

    def process_test(self, sess, data_config):
        data = {}
        done = False
        # Set target episode beginning to be at current timepoint:
        data_config['trial_config']['align_left'] = 1
        self.log.info('test episode started...')

        while not done:
            sess.run(self.sync_pi)

            data = self.get_data(
                policy=self.local_network,
                data_sample_config=data_config
            )
            done = np.asarray(data['terminal']).any()

            # self.process_summary(sess, data)

            # self.log.warning('test episode done: {}'.format(done))

            self.global_timestamp = data['on_policy'][0]['state']['metadata']['timestamp'][-1]

            # # Wait for other workers to catch up with training:
            # start_global_step = sess.run(self.global_step)
            # while self.test_skeep_steps >= sess.run(self.global_step) - start_global_step:
            #     time.sleep(self.test_sleep_time)

            self.log.info(
                'test episode rollout done, global_time: {}, global_step: {}'.format(
                    datetime.datetime.fromtimestamp(self.global_timestamp),
                    sess.run(self.global_step)
                )
            )

        self.log.notice(
            'test episode finished, global_time: {}'.format(
                datetime.datetime.fromtimestamp(self.global_timestamp)
            )
        )
        self.log.notice(
            'final value: {} after {} steps @ {}'.format(
                data['on_policy'][0]['info']['broker_value'][-1],
                data['on_policy'][0]['info']['step'][-1],
                data['on_policy'][0]['info']['time'][-1],
            )
        )

        self.process_summary(sess, data)

    def process_train(self, sess, data_config):
        data = {}
        done = False
        # Set source episode to be sampled uniformly from test interval:
        data_config['trial_config']['align_left'] = 0
        # self.log.warning('train episode started...')

        while not done:
            sess.run(self.sync_pi)

            wirte_model_summary = \
                self.local_steps % self.model_summary_freq == 0

            data = self.get_data(
                policy=self.local_network,
                data_sample_config=data_config
            )
            done = np.asarray(data['terminal']).any()
            feed_dict = self.process_data(sess, data, is_train=True, pi=self.local_network)

            if wirte_model_summary:
                fetches = [self.train_op, self.model_summary_op, self.inc_step]
            else:
                fetches = [self.train_op, self.inc_step]

            fetched = sess.run(fetches, feed_dict=feed_dict)

            if wirte_model_summary:
                model_summary = fetched[-2]

            else:
                model_summary = None

            self.process_summary(sess, data, model_summary)

            self.local_steps += 1

        # self.log.warning(
        #     'train episode finished at {} vs was_global_time: {}'.format(
        #         data['on_policy'][0]['info']['time'][-1],
        #         datetime.datetime.fromtimestamp(data['on_policy'][0]['state']['metadata']['timestamp'][-1])
        #
        #     )
        # )

