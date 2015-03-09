from explorer.utils import passes_blacklist, swap_params, extract_params, shared_dict_update, get_connection
from django.db import models, DatabaseError
from time import time
from django.core.urlresolvers import reverse
from django.conf import settings
import app_settings

MSG_FAILED_BLACKLIST = "Query failed the SQL blacklist."


class Query(models.Model):
    title = models.CharField(max_length=255)
    sql = models.TextField()
    description = models.TextField(null=True, blank=True)
    created_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_run_date = models.DateTimeField(auto_now=True)

    def __init__(self, *args, **kwargs):
        self.params = kwargs.get('params')
        kwargs.pop('params', None)
        super(Query, self).__init__(*args, **kwargs)

    class Meta:
        ordering = ['title']
        verbose_name_plural = 'Queries'

    def __unicode__(self):
        return unicode(self.title)

    def passes_blacklist(self):
        return passes_blacklist(self.final_sql())

    def final_sql(self):
        return swap_params(self.sql, self.params)

    def try_execute(self):
        """
        A lightweight version of .execute to just check the validity of the SQL.
        Skips the processing associated with QueryResult.
        """
        QueryResult(self.final_sql())

    def execute(self):
        ret = QueryResult(self.final_sql())
        ret.process()
        return ret

    def available_params(self):
        """
            Merge parameter values into a dictionary of available parameters

        :param param_values: A dictionary of Query param values.
        :return: A merged dictionary of parameter names and values. Values of non-existent parameters are removed.
        """

        p = extract_params(self.sql)
        if self.params:
            shared_dict_update(p, self.params)
        return p

    def get_absolute_url(self):
        return reverse("query_detail", kwargs={'query_id': self.id})

    def log(self, user):
        log_entry = QueryLog(sql=self.sql, query_id=self.id, run_by_user=user, is_playground=not bool(self.id))
        log_entry.save()


class QueryLog(models.Model):

    sql = models.TextField()
    query = models.ForeignKey(Query, null=True, blank=True, on_delete=models.SET_NULL)
    is_playground = models.BooleanField(default=False)
    run_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True)
    run_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-run_at']


class QueryResult(object):

    def __init__(self, sql):

        self.sql = sql

        cursor, duration = self.execute_query()

        self._description = cursor.description or []
        self._data = [list(r) for r in cursor.fetchall()]
        self.duration = duration

        cursor.close()

        self._headers = self._get_headers()
        self._summary = {}

    @property
    def data(self):
        return self._data or []

    @property
    def headers(self):
        return self._headers or []

    @property
    def summary(self):
        return self._summary or {}

    def _get_headers(self):
        return [d[0] for d in self._description] if self._description else ['--']

    def _get_numerics(self):

        conn = get_connection()
        if hasattr(conn.Database, "NUMBER"):
            return [(ix, c.name) for ix, c in enumerate(self._description) if hasattr(c, 'type_code') and c.type_code in conn.Database.NUMBER.values]
        elif self.data:
            d = self.data[0]
            return [(ix, c[0]) for ix, c in enumerate(self._description) if not isinstance(d[ix], basestring) and unicode(d[ix]).isnumeric()]
        return []

    def _get_unicodes(self):
        if len(self.data):
            return [ix for ix, c in enumerate(self.data[0]) if type(c) is unicode]
        return []

    def _get_transforms(self):
        transforms = app_settings.EXPLORER_TRANSFORMS
        return [(self.headers.index(field), template) for field, template in transforms if field in self.headers]

    def column(self, ix):
        return [r[ix] for r in self.data]

    def process(self):
        self._summary = [ColumnSummary(header, self.column(ix)) for ix, header in self._get_numerics()]

        unicodes = self._get_unicodes()
        transforms = self._get_transforms()
        for r in self.data:
            for u in unicodes:
                r[u] = r[u].encode('utf-8')
            for ix, t in transforms:
                r[ix] = t.format(str(r[ix]))

    def execute_query(self):
        conn = get_connection()
        cursor = conn.cursor()
        start_time = time()

        try:
            cursor.execute(self.sql)
        except DatabaseError as e:
            cursor.close()
            raise e

        end_time = time()
        duration = (end_time - start_time) * 1000
        return cursor, duration


class ColumnStat(object):

    def __init__(self, label, statfn, precision=2):
        self.label = label
        self.statfn = statfn
        self.precision = precision

    def __call__(self, coldata):
        self.value = round(float(self.statfn(coldata)), self.precision)

    def __unicode__(self):
        return self.label

    def foo(self):
        return "foobar"


class ColumnSummary(object):

    _stats = [
        ColumnStat("Sum", sum),
        ColumnStat("Length", len, 0),
        ColumnStat("Average", lambda x: float(sum(x)) / float(len(x))),
        ColumnStat("Minimum", min),
        ColumnStat("Maximum", max)
    ]

    def __init__(self, header, col):
        self.name = header
        for stat in self._stats:
            stat(col)

    @property
    def stats(self):
        return {c.label: c.value for c in self._stats}

    def __unicode__(self):
        return self.name
