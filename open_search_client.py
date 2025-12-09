from opensearchpy import OpenSearch

INDEX_NAME = "rag_practice_2"

def get_opensearch_client():
    client = OpenSearch("http://localhost:9200")
    # client.index(
    #     index=INDEX_NAME,
    #     body={"message": "Hello OpenSearch!"}
    # )
    print(f"OpenSearch: {client.info()}")
    return client
