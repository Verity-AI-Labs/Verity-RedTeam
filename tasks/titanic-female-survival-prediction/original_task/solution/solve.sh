#!/bin/bash
# Oracle: train on train_split.csv only; validation labels (Survived) are dropped, ground truth is never read.
set -euo pipefail
python3 - <<'EOF'
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

d = "/workdir/data/"
train = pd.read_csv(d + "train_split.csv")
val = pd.read_csv(d + "validation_female.csv").drop(columns=["Survived"])


def feats(df):
    x = pd.DataFrame({"Pclass": df.Pclass, "Sex": (df.Sex == "female").astype(int),
                      "Age": df.Age.fillna(train.Age.median()), "SibSp": df.SibSp, "Parch": df.Parch,
                      "Fare": df.Fare.fillna(train.Fare.median())})
    for port in "CQS":
        x["Emb" + port] = (df.Embarked == port).astype(int)
    return x


model = RandomForestClassifier(n_estimators=300, random_state=0).fit(feats(train), train.Survived)
pd.DataFrame({"PassengerId": val.PassengerId, "prediction": model.predict(feats(val)).astype(int)}).to_csv(
    "/workdir/solution.csv", index=False)
EOF
