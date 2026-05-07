import pandas as pd

class FeatureEngineering:

    def build_cashflow_features(self, df):
        df["day_index"] = range(len(df))
        return df

    def build_fraud_features(self, df):
        df["frequency"] = df.groupby("user_id")["user_id"].transform("count")
        return df