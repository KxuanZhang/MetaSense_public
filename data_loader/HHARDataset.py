import torch.utils.data
import pandas as pd
import time
import numpy as np
import sys

sys.path.append('..')
import options
import itertools


opt = options.HHAROpt
OVERLAPPING_WIN_LEN = opt['seq_len'] // 2
WIN_LEN = opt['seq_len']


class KSHOTTensorDataset(torch.utils.data.Dataset):
    def __init__(self, num_classes, features, classes, domains):
        assert (features.shape[0] == classes.shape[0] == domains.shape[0])

        self.num_classes = num_classes
        self.features_per_class = []
        self.classes_per_class = []
        self.domains_per_class = []

        for class_idx in range(self.num_classes):
            indices = np.where(classes == class_idx)
            self.features_per_class.append(np.random.permutation(features[indices]))
            self.classes_per_class.append(np.random.permutation(classes[indices]))
            self.domains_per_class.append(np.random.permutation(domains[indices]))

        self.data_num = min(
            [len(feature_per_class) for feature_per_class in self.features_per_class])  # get min number of classes

        for i in range(self.num_classes):
            self.features_per_class[i] = torch.from_numpy(self.features_per_class[i][:self.data_num]).float()
            self.classes_per_class[i] = torch.from_numpy(self.classes_per_class[i][:self.data_num])
            self.domains_per_class[i] = torch.from_numpy(self.domains_per_class[i][:self.data_num])

    def __getitem__(self, index):

        features = torch.FloatTensor(self.num_classes, *(self.features_per_class[0][0].shape)) # make FloatTensor with shape num_classes x F-dim1 x F-dim2...
        classes = torch.LongTensor(self.num_classes)
        domains = torch.LongTensor(self.num_classes)

        rand_indices = [i for i in range(self.num_classes)]
        np.random.shuffle(rand_indices)

        for i in range(self.num_classes):
            features[i] = self.features_per_class[rand_indices[i]][index]
            classes[i] = self.classes_per_class[rand_indices[i]][index]
            domains[i] = self.domains_per_class[rand_indices[i]][index]

        return (features, classes, domains)

    def __len__(self):
        return self.data_num


class HHARDataset(torch.utils.data.Dataset):
    # load static files

    def __init__(self, file, transform=None, model=None, device=None, user=None, gt=None, complementary=False):
        """
        Args:
            file_path (string): Path to the csv file with annotations.
            transform (callable, optional): Optional transform to be applied
                on a sample.
            gt: condition on action
            user: condition on user
            model: condition on model
            device: condition on device instance
            complementary: is it complementary dataset for given conditions? (used for "multi" case)
        """
        st = time.time()
        self.user = user
        self.model = model
        self.device = device
        self.gt = gt
        self.complementary = complementary

        self.df = pd.read_csv(file)
        if complementary:  # for multi domain
            if user:
                self.df = self.df[self.df['User'] != user]
            if model:
                self.df = self.df[self.df['Model'] != model]
            if device:
                self.df = self.df[self.df['Device'] != device]
            if gt:
                self.df = self.df[self.df['gt'] != gt]
        else:

            if user:
                self.df = self.df[self.df['User'] == user]
            if model:
                self.df = self.df[self.df['Model'] == model]
            if device:
                self.df = self.df[self.df['Device'] == device]
            if gt:
                self.df = self.df[self.df['gt'] == gt]
            print(len(self.df))

        self.transform = transform
        ppt = time.time()

        self.preprocessing()
        print('Loading data done with rows:{:d}\tPreprocessing:{:f}\tTotal Time:{:f}'.format(len(self.df.index),
                                                                                             time.time() - ppt,
                                                                                             time.time() - st))

    def preprocessing(self):
        self.num_domains = 0
        self.features = []
        self.class_labels = []
        self.domain_labels = []

        self.datasets = []  # list of dataset per each domain
        self.kshot_datasets = []  # list of dataset per each domain

        if self.complementary:
            users = set(options.HHAROpt['users']) - set(self.user)  # currently supports only user and model
            models = set(options.HHAROpt['models']) - set(self.model)

        else:
            users = set([self.user])  # bracket is required to append a tuple
            models = set([self.model])

        domain_superset = list(itertools.product(models, users))
        self.domain_superset = domain_superset
        valid_domains = []

        for idx in range(max(len(self.df) // OVERLAPPING_WIN_LEN - 1, 0)):
            user = self.df.iloc[idx * OVERLAPPING_WIN_LEN, 9]
            model = self.df.iloc[idx * OVERLAPPING_WIN_LEN, 10]
            device = self.df.iloc[idx * OVERLAPPING_WIN_LEN, 11]
            class_label = self.df.iloc[idx * OVERLAPPING_WIN_LEN, 12]
            domain_label = -1

            for i in range(len(domain_superset)):
                if domain_superset[i] == (model, user) and domain_superset[i] not in valid_domains:
                    valid_domains.append(domain_superset[i])
                    break

            if (model, user) in valid_domains:
                domain_label = valid_domains.index((model, user))
            else:
                continue


            feature = self.df.iloc[idx * OVERLAPPING_WIN_LEN:(idx + 2) * OVERLAPPING_WIN_LEN, 3:9].values
            feature = feature.T

            self.features.append(feature)
            self.class_labels.append(self.class_to_number(class_label))
            self.domain_labels.append(domain_label)

        self.num_domains = len(valid_domains)
        self.features = np.array(self.features, dtype=np.float)
        self.class_labels = np.array(self.class_labels)
        self.domain_labels = np.array(self.domain_labels)

        # append dataset for each domain
        for domain_idx in range(self.num_domains):
            indices = np.where(self.domain_labels == domain_idx)[0]
            self.datasets.append(torch.utils.data.TensorDataset(torch.from_numpy(self.features[indices]).float(),
                                                                torch.from_numpy(self.class_labels[indices]),
                                                                torch.from_numpy(self.domain_labels[indices])))
            kshot_dataset= KSHOTTensorDataset(len(np.unique(self.class_labels)),
                                                          self.features[indices],
                                                          self.class_labels[indices],
                                                          self.domain_labels[indices])
            self.kshot_datasets.append(kshot_dataset)


        # concated dataset
        self.dataset = torch.utils.data.ConcatDataset(self.datasets)

        print('Valid domains:' + str(valid_domains))


    def __len__(self):
        # return max(len(self.df) // OVERLAPPING_WIN_LEN - 1, 0)
        return len(self.dataset)

    def get_num_domains(self):
        return self.num_domains

    def get_datasets_per_domain(self):
        return self.kshot_datasets

    def class_to_number(self, label):
        dic = {'bike': 0,
               'sit': 1,
               'stand': 2,
               'walk': 3,
               'stairsup': 4,
               'stairsdown': 5,
               'null': 6}
        return dic[label]

    def __getitem__(self, idx):
        if isinstance(idx, torch.Tensor):
            idx = idx.item()

        return self.dataset[idx]



if __name__ == '__main__':
    pass