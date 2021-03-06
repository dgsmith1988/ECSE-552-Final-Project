from datetime import datetime
import timeit
import os
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from torch.utils.data import DataLoader
from feature_extraction import AudioDataset
from dict_logger import DictLogger
import json
import optuna
from torch.utils.data import random_split
from pytorch_lightning.callbacks import EarlyStopping
from sklearn.metrics import ConfusionMatrixDisplay, confusion_matrix, roc_auc_score
import pytorch_lightning as pl
import torch
import pandas as pd

BASELINE_RESNET_NAME = "Baseline Resnet"
MEL_AE_NAME = "Mel AE"

def init_measurements_path():
    print("Creating measurements path...")

    is_colab = 'COLAB_GPU' in os.environ

    if is_colab:
        print('Running on Colab')
        measurements_dir = '/content/drive/MyDrive/ECSE-552-FP/Measurements/'
    else:
        print('Not running on Colab')
        measurements_dir = './Measurements/'

    now = datetime.today().strftime("%b-%d-%Y")
    measurements_path = os.path.join(measurements_dir, now)

    if not os.path.isdir(measurements_dir):
        try:
            os.mkdir(measurements_dir)
        except OSError as error:
            print(error)

    if not os.path.isdir(measurements_path):
        try:
            os.mkdir(measurements_path)
        except OSError as error:
            print(error)

    return measurements_path


def init_dataset(data_dir, dur_seconds, crop=None, rgb_expand=False):
    print("Creating dataset")
    start_time = timeit.default_timer()
    dataset = AudioDataset(data_dir, dur_seconds, rgb_expand, crop)
    end_time = timeit.default_timer()
    print("\nDataset creation in seconds: ", end_time - start_time)

    return dataset


def split_dataset(dataset, train_split=.8):
    num_samples = len(dataset)
    num_train = np.floor(num_samples * train_split).astype(int)
    num_val = num_samples - num_train
    train_dataset, val_dataset = random_split(dataset, [num_train, num_val])
    return train_dataset, val_dataset


def get_datasets(data_dir, dur_seconds, train_split=.8, crop=None, rgb_expand=False):
    dataset = init_dataset(data_dir, dur_seconds, crop, rgb_expand)
    train_dataset, val_dataset = split_dataset(dataset, train_split)
    return train_dataset, val_dataset


def make_log_filenames(comment):
    now = datetime.now().strftime("%H_%M_%S-")

    profiler_filename = f"{comment}{now}profiler_output"
    plot_filename = f"{comment}{now}Loss-Acc.png"

    return profiler_filename, plot_filename, now


def init_trainer(logger, max_epochs, profiler, early_stopping = True):
    print("Initializing trainer...")

    is_colab = 'COLAB_GPU' in os.environ

    if early_stopping:
    	callbacks = [EarlyStopping('val_loss'),  
                    pl.callbacks.ModelCheckpoint(
                    monitor="val_loss",
                    dirpath='./Checkpoints',
                    mode='min',
                    filename='{epoch:02d}-{val_acc_step:.2f}')]
    else:
    	callbacks = [pl.callbacks.ModelCheckpoint(
                    monitor="val_loss",
                    dirpath='./Checkpoints',
                    mode='min',
                    filename='{epoch:02d}-{val_acc_step:.2f}')]

    if is_colab:
        trainer = pl.Trainer(gpus=-1, auto_select_gpus=True, callbacks=callbacks,
                             logger=logger, max_epochs=max_epochs, profiler=profiler)
    else:
        trainer = pl.Trainer(callbacks=callbacks,
                             logger=logger, max_epochs=max_epochs, profiler=profiler)

    return trainer


def plot_logger_metrics(logger, measurements_path, plot_filename):
    save_path = os.path.join(measurements_path, plot_filename + '-metrics.json')
    with open(save_path, 'w') as f:
        json.dump(logger.metrics, f, indent=4)

    f, axs = plt.subplots(1, 2, figsize=(15, 5))
    font = {'size': 14}
    matplotlib.rc('font', **font)

    axs[0].plot(logger.metrics['train_loss'], lw=3, ms=8, marker='o', color='orange', label='Train')
    axs[0].set_title("Train/Val Loss")
    axs[0].set_ylabel("Loss")
    axs[0].plot(logger.metrics['val_loss'], lw=3, ms=10, marker='^', color='purple', label='Validation')
    axs[0].set_title('Classifer\nTrain/Val Loss Over Time')
    axs[0].set_xlabel("Epochs")
    axs[0].grid()

    axs[1].plot(logger.metrics['train_acc_epoch'], lw=3, ms=8, marker='o', color='orange', label='Train')
    axs[1].set_title("Classifer\nTrain/Val Accuracy")
    axs[1].set_ylabel("Accuracy")
    axs[1].plot(logger.metrics['val_acc_epoch'], lw=3, ms=10, marker='^', color='purple', label='Validation')
    axs[1].set_title('Classifier\nTrain/Val Accuracy Over Time')
    axs[1].set_xlabel("Epochs")
    axs[1].grid()

    plt.legend(loc='lower right')
    plt.savefig(os.path.join(measurements_path, plot_filename))
    plt.show()


def plot_confusion_matrix(model, model_name, dataset, data_name, batch_size, measurements_path, plot_time):
    print("Generating Confusion Matrices")
    class_names = dataset.dataset.dirs
    dataloader = DataLoader(dataset, batch_size=batch_size)

    conf_mat = torch.zeros([len(class_names), len(class_names)])
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)
    for batch in dataloader:
        x, y = batch
        y_hat = model(x.to(device))
        # convert the logit to a class prediction
        y_hat = y_hat.softmax(dim=1)
        y_hat = y_hat.argmax(dim=1)
        conf_mat += confusion_matrix(y.cpu(), y_hat.cpu(), labels=list(range(len(class_names))))

    title = model_name + "\nConfusion Matrix - " + data_name
    disp = ConfusionMatrixDisplay(conf_mat.numpy(), display_labels=class_names)
    png_filename = model_name + plot_time + "ConfMat" + "-" + data_name + "-raw" + ".png"
    csv_filename = model_name + plot_time + "ConfMat" + "-" + data_name + "-raw" + ".csv"
    disp.plot(cmap=plt.cm.Blues)
    disp.ax_.set_title(title)
    plt.savefig(os.path.join(measurements_path, png_filename))
    conf_mat_df = pd.DataFrame(conf_mat.numpy())
    conf_mat_df.to_csv(os.path.join(measurements_path, csv_filename))

    # Print accuracy
    corr = 0
    for i in range(conf_mat.shape[0]):
      corr += conf_mat[i,i].numpy()
    acc = (corr/conf_mat.sum()).numpy()
    print("Accuracy: "+ str(round(acc*100, 2))+"%")

    # normalize the data for another view
    for j in range(conf_mat.shape[0]):
      conf_mat[j] = conf_mat[j]/conf_mat[j].sum()
    #conf_mat = conf_mat/conf_mat.sum()
    disp = ConfusionMatrixDisplay(conf_mat.numpy(), display_labels=class_names)
    png_filename = model_name + plot_time + "ConfMat" + "-" + data_name + "-norm" + ".png"
    csv_filename = model_name + plot_time + "ConfMat" + "-" + data_name + "-norm" + ".csv"
    disp.plot(cmap=plt.cm.Blues)
    disp.ax_.set_title(title)
    plt.savefig(os.path.join(measurements_path, png_filename))
    conf_mat_df = pd.DataFrame(conf_mat.numpy())
    conf_mat_df.to_csv(os.path.join(measurements_path, csv_filename))
    
    return conf_mat


def get_auroc(model, dataset, batch_size):
    # This is broken and needs to be fixed...
    print("Calculating AUROC")
    dataloader = DataLoader(dataset, batch_size=batch_size)
    class_names = dataset.dataset.dirs

    y_pop_hat = np.zeros([0,3])
    y_pop_true = np.zeros([0,0], dtype=np.int64)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)
    for batch in dataloader:
        x, y = batch
        y_hat = model(x.to(device))
        # convert the logit to a class prediction
        y_hat = y_hat.softmax(dim=1)
        # y_hat = y_hat.argmax(dim=1)
        y_pop_true = np.append(y_pop_true, y.numpy())
        y_pop_hat = np.append(y_pop_hat, y_hat.detach().numpy())

    auroc = roc_auc_score(y_pop_true, y_pop_hat, labels=class_names, multi_class="ovr")
    print("AUROC = " + str(round(auroc, 4)))


def train_model(model, name, train_dataset, val_dataset, max_epoch=5, batch_size=10, early_stopping = True):
    train_loader = DataLoader(train_dataset, batch_size=batch_size)
    val_loader = DataLoader(val_dataset, batch_size=batch_size)

    measurements_path = init_measurements_path()
    profiler_filename, plot_filename, plot_time = make_log_filenames(name)

    logger = DictLogger()
    profiler = pl.profiler.SimpleProfiler(dirpath=measurements_path, filename=profiler_filename)

    trainer = init_trainer(logger, max_epoch, profiler, early_stopping)

    trainer.fit(model, train_loader, val_loader)

    if name == BASELINE_RESNET_NAME:
        plot_logger_metrics(logger, measurements_path, plot_filename)
        # plot_confusion_matrix(model, name, train_dataset, "Training", batch_size, measurements_path, plot_time)
        # plot_confusion_matrix(model, name, val_dataset, "Validation", batch_size, measurements_path, plot_time)
        get_auroc(model, val_dataset, batch_size)
    elif name == MEL_AE_NAME:
        # Todo: Determine how the logger will interact with this particular model. Metrics might need to be added in the
        #  different "end" functions to facilitate this. A more generic "plot_logger_metrics" function would help achieve
        #  this and allow these models to both be called with the same training function.
        pass
        # plot_logger_metrics(logger, measurements_path, plot_filename)
    
    return logger


def hp_tuning_voxforge_classifier(data_dir, max_epoch=10, batch_size=10, dur_seconds=5, comment=""):
    # Hyperparameter tuning
    def objective(trial):
        model = BaselineResnetClassifier(num_classes=3)

        logger = DictLogger()
        checkpoint_callback = pl.callbacks.ModelCheckpoint(
            monitor="val_acc_step",
            dirpath='./Checkpoints',
            mode='max',
            filename='{epoch:02d}-{val_acc_step:.2f}'
        )

        trainer = pl.Trainer(
            logger=logger,
            max_epochs=5,
            gpus=1 if torch.cuda.is_available() else None,
            callbacks=[checkpoint_callback
                       ],
        )

        # we optimize max_t and batch_size
        max_t = trial.suggest_int("max_t", 1, 5)
        batch_size = trial.suggest_int('batch_size', 4, 64, log=True)

        # Prepare and split dataset.
        print(f"Preparing and splitting dataset...")

        name = "Resnet50 Baseline"
        start_time = timeit.default_timer()
        dataset = AudioDataset(data_dir, max_t=max_t)
        end_time = timeit.default_timer()
        print("Dataset creation in seconds: ", end_time - start_time)

        num_samples = len(dataset)
        num_train = np.floor(num_samples * 0.8).astype(int)
        num_val = num_samples - num_train

        train_dataset, val_dataset = torch.utils.data.random_split(dataset, [num_train, num_val],
                                                                   generator=torch.Generator().manual_seed(42))

        train_loader = DataLoader(train_dataset, batch_size=batch_size)
        val_loader = DataLoader(val_dataset, batch_size=batch_size)

        hyperparameters = dict(max_t=max_t, batch_size=batch_size)
        trainer.logger.log_hyperparams(hyperparameters)
        trainer.fit(model, train_loader, val_loader)

        return trainer.callback_metrics["val_acc_step"].item()

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=25)

    print("Number of finished trials: {}".format(len(study.trials)))

    print("Best trial:")
    trial = study.best_trial

    print("  Value: {}".format(trial.value))

    print("  Params: ")
    for key, value in trial.params.items():
        print("    {}: {}".format(key, value))

    return trainer.checkpoint_callback.best_model_path, trial.params, study


if __name__ == "__main__":
    from models import BaselineResnetClassifier, Mel_ae

    data_dir = "E:\\Temp\\Voice Data"
    # model_name = MEL_AE_NAME
    model_name = BASELINE_RESNET_NAME

    if model_name == BASELINE_RESNET_NAME:
        model = BaselineResnetClassifier(num_classes=3)
        train_dataset, val_dataset = get_datasets(data_dir=data_dir, dur_seconds=3, train_split=.8, crop=None,
                                                  rgb_expand=False)
        train_model(model, model_name, train_dataset, val_dataset, max_epoch=2, batch_size=10)
    elif model_name == MEL_AE_NAME:
        input_height = 128
        model = Mel_ae(input_height, enc_type='resnet50', first_conv=False, maxpool1=False, enc_out_dim=2048,
                       kl_coeff=0.1, latent_dim=3)
        train_dataset, val_dataset = get_datasets(data_dir=data_dir, dur_seconds=5, train_split=.8, crop=input_height,
                                                  rgb_expand=True)
        train_model(model, model_name, train_dataset, val_dataset, max_epoch=20, batch_size=10)
