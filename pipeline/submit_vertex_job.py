"""Programmatic Vertex AI Custom Training Job Submitter.

Submits a cloud-native Custom Python Job to Google Cloud Vertex AI.
The training script will run entirely on a high-powered GCP L4 GPU instance,
read features from BigQuery, download training sets from GCS, train XGBoost with CUDA,
and upload results back to GCS.
"""
import os
from google.cloud import aiplatform

def submit_cloud_training():
    project_id = "eastwest72hack26bos-518"
    bucket_name = "eastwest72hack26bos-518-research"
    location = "us-central1"
    
    print(f"Initializing Vertex AI Platform connection (Project: {project_id}, Region: {location})...")
    aiplatform.init(project=project_id, location=location, staging_bucket=f"gs://{bucket_name}")
    
    print("\n[Step 1] Creating Custom Job from local script...")
    # We use a standard TensorFlow CPU prebuilt container and pass our packages in requirements to install them dynamically.
    container_uri = "us-docker.pkg.dev/vertex-ai/training/tf-cpu.2-12.py310:latest"
    script_path = "pipeline/cloud_gpu_trainer_script.py"
    
    job = aiplatform.CustomJob.from_local_script(
        display_name="spp-climatology-grid-stress-cpu-ensemble",
        script_path=script_path,
        container_uri=container_uri,
        requirements=[
            "numpy==1.26.4",
            "pandas==2.1.4",
            "scikit-learn==1.3.2",
            "xgboost==1.7.6",
            "lightgbm==4.1.0",
            "pyarrow==14.0.1",
            "google-cloud-bigquery==3.15.0",
            "google-cloud-storage==2.14.0",
            "google-cloud-aiplatform==1.38.1",
            "db-dtypes==1.2.0",
            "joblib==1.3.2"
        ],
        args=["--bucket-name", bucket_name],
        replica_count=1,
        machine_type="n1-highcpu-16", # High-performance parallelized Cloud CPUs
    )
    
    print("\n[Step 2] Submitting Job to Google Cloud Vertex AI (Running on remote 16-CPU Cloud Instance)...")
    print("This will execute entirely in the cloud. Polling remote logs...")
    job.run(sync=True)
    
    print("\n[Step 3] Vertex AI Training Job successfully completed on Google Cloud!")
    print(f"Model outputs are saved in your GCS bucket: gs://{bucket_name}/models/")

if __name__ == "__main__":
    submit_cloud_training()
