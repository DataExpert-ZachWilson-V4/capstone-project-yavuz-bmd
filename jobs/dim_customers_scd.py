from pyspark.sql import SparkSession
from utils import db_utils, spark_utils, get_customers_by_date_range
from delta.tables import DeltaTable
from pyspark.sql.functions import *
from datetime import datetime, timezone

def dim_customers_scd_etl_job(spark: SparkSession, start_date:str, end_date:str):

    table_name = "dim_customers_scd"

    current_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    schema = db_utils.get_metadata(table_name)["schema"]
    delta_table_path = db_utils.get_metadata(table_name)["delta_table_path"]

    # Check if the Delta table exists
    if DeltaTable.isDeltaTable(spark, delta_table_path):
        delta_table = DeltaTable.forPath(spark, delta_table_path)
    else:
        raise Exception("Delta Table does NOT exists!")

    new_data = get_customers_by_date_range.collect_customer_data(start_date, end_date)

    if new_data == []:
        print("There is no change, hence df is empty. Nothing to write!")
        return

    new_df = spark.createDataFrame(new_data) \
        .withColumn("effective_start_date", lit(None)) \
        .withColumn("effective_end_date", lit(None)) \
        .withColumn("is_active", lit(None)) if new_data != [] else spark.createDataFrame(new_data, schema=schema)

    # Cast the DataFrame to the desired schema
    new_df_casted = db_utils.cast_to_schema(new_df, schema)
    # new_df_casted.printSchema()
    # print("new_records")
    # new_df_casted.orderBy(col("updated_at").desc()).show()

    # print("delta")
    delta_df = delta_table.toDF() \
        .filter(col("is_active") == True)
    # delta_df.show()

    joined = new_df_casted.alias("src") \
        .join(delta_df.alias("tgt"), col("tgt.customer_id") == col("src.customer_id"), "left")
        
    updated_new_records = joined.filter(
            (col("tgt.customer_id") == col("src.customer_id")) & \
            ((col("tgt.first_name") != col("src.first_name")) | \
            (col("tgt.last_name") != col("src.last_name")) | \
            (col("tgt.email") != col("src.email")) | \
            (col("tgt.phone") != col("src.phone")) | \
            (col("tgt.number_of_orders") != col("src.number_of_orders")))
        ).select(
            col("src.customer_id"),
            col("src.email"),
            col("src.first_name"),
            col("src.last_name"),
            col("src.phone"),
            col("src.number_of_orders"),
            col("src.updated_at"),
            to_date(lit(current_date_utc), "yyyy-MM-dd").alias("effective_start_date"),
            to_date(lit("9999-12-31"), "yyyy-MM-dd").alias("effective_end_date"),
            lit(True).alias("is_active")
        )

    # print("updated_new_records")
    # updated_new_records.show()

    updated_old_records = joined.filter(
            (col("tgt.customer_id") == col("src.customer_id")) & \
            ((col("tgt.first_name") != col("src.first_name")) | \
            (col("tgt.last_name") != col("src.last_name")) | \
            (col("tgt.email") != col("src.email")) | \
            (col("tgt.phone") != col("src.phone")) | \
            (col("tgt.number_of_orders") != col("src.number_of_orders")))
        ).select(
            col("tgt.customer_id"),
            col("tgt.email"),
            col("tgt.first_name"),
            col("tgt.last_name"),
            col("tgt.phone"),
            col("tgt.number_of_orders"),
            col("tgt.updated_at"),
            col("tgt.effective_start_date"),
            to_date(lit(current_date_utc), "yyyy-MM-dd").alias("effective_end_date"),
            lit(False).alias("is_active")
        )

    # print("updated_old_records")
    # updated_old_records.show()

    new_records = joined.filter(col("src.customer_id").isNotNull() & col("tgt.customer_id").isNull()) \
        .select(
            col("src.customer_id"),
            col("src.email"),
            col("src.first_name"),
            col("src.last_name"),
            col("src.phone"),
            col("src.number_of_orders"),
            col("src.updated_at"),
            to_date(lit(current_date_utc), "yyyy-MM-dd").alias("effective_start_date"),
            to_date(lit("9999-12-31"), "yyyy-MM-dd").alias("effective_end_date"),
            lit(True).alias("is_active")
        )

    # print("new_records")
    # new_records.show()

    final_changes = updated_old_records.union(updated_new_records).union(new_records)

    # print("final changes")
    # final_changes.show()

    # Perform the merge operation
    print("Performing Writing Operation")
    delta_table.alias("tgt").merge(
        final_changes.alias("src"),
        """tgt.customer_id = src.customer_id AND 
        tgt.email = src.email AND 
        tgt.first_name = src.first_name AND 
        tgt.last_name = src.last_name AND 
        tgt.phone = src.phone AND
        tgt.number_of_orders = src.number_of_orders"""
    ).whenMatchedUpdateAll() \
    .whenNotMatchedInsertAll() \
    .execute()

    print("writing operation is completed")
    db_utils.run_glue_crawler("dimCustomersScdCrawler")
    print("Crawler run is completed")
