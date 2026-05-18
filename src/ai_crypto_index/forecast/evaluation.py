import numpy as np


def evaluate_lstm(model, X_test, y_test, plot = False):
    if X_test is None or y_test is None:
        print("[WARN] evaluate_lstm: no test set (None) — skipping evaluation.")
        return None
    if getattr(X_test, "size", 0) == 0 or getattr(y_test, "size", 0) == 0:
        print("[WARN] evaluate_lstm: empty test set — skipping evaluation.")
        return None

    preds = model.predict(X_test, verbose=0).flatten()
    
    # Simple error metric (MSE or MAE)
    mse = np.mean((preds - y_test) ** 2)
    print("Test MSE:", mse)

    # Reproduce the plot if needed
    if plot:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(8,4))
        plt.plot(y_test, label="True", linestyle='--')
        plt.plot(preds, label="Predicted")
        plt.legend()
        plt.title("LSTM prediction on test set")
        plt.show()
    
    return preds