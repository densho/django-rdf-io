from django.db import models
from django.utils.translation import ugettext_lazy as _
# for django 1.7 +
#from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator
#from django.db.models.fields.related import ReverseSingleRelatedObjectDescriptor
from django.db.models.fields.related_descriptors import ForwardManyToOneDescriptor

import itertools

# helpers


def getattr_path(obj, path):
    try:
        return _getattr_related(obj, obj, path.replace('__', '.').replace("/", ".").split('.'))

    except ValueError as e:
        import traceback
#        import pdb; pdb.set_trace()
        raise ValueError("Failed to map '{}' on '{}' (cause {})".format(path, obj, e))


def dequote(s):
    """
    If a string has single or double quotes around it, remove them.
    todo: Make sure the pair of quotes match.
    If a matching pair of quotes is not found, return the string unchanged.
    """
    if s.startswith(("'", '"', '<')):
        return s[1:-1]
    return s


def _apply_filter(val, filter, localobj, rootobj):
    """
        Apply a simple filter to a specific property, with a list of possible values
    """
    for targetval in filter.replace(" OR ", ",").split(","):
        tval = dequote(targetval)
        if tval.startswith('^'):
            tval = getattr(rootobj, tval[1:])
        elif tval.startswith('.'):
            tval = getattr(localobj, tval[1:])
        if tval == 'None':
            return bool(val)
        elif tval == 'NotNone':
            return not bool(val)
        elif val == tval:
            return True
    return False


def apply_pathfilter(obj, filter_expr):
    """
        apply a filter based on a list of path expressions  path1=a,b AND path2=c,db
    """
    and_clauses = filter_expr.split(" AND ")

    for clause in and_clauses:

        (path, vallist) = clause.split("=")
        if path[:-1] == '!':
            negate = True
            path = path[0:-1]
        else:
            negate = False
        or_vals = vallist.split(",")
        # if multiple values - only one needs to match
        matched = False
        for val in getattr_path(obj, path):
            for match in or_vals:
                if match == 'None':
                    matched = negate ^ (not val)
                elif type(val) == bool:
                    matched = negate ^ (val == (match == 'True'))
                    break
                else:
                    if negate ^ (val == match):
                        matched = True
                        break
            if matched:
                # dont need to check any mor - continue with the AND loop
                break
        # did any value match?
        if not matched:
            return False

    return True


def _getattr_related(rootobj, obj, fields):
    """
        get an attribute - if multi-valued will be a list object!
        fields may include filters.  
    """
    # print obj, fields
    if not len(fields):
        return [obj]

    field = fields.pop(0)
    filter = None
    # try to get - then check for django 1.7+ manager for related field
    try:
        # check for lang
        try:
            (field, langfield) = field.split('@')
            if langfield[0] in ["'", '"']:
                lang = langfield[1:-1]
            else:
                lang = _getattr_related(rootobj, obj, [langfield, ] + fields).pop(0)
                fields = []
        except:
            lang = None
        # check for datatype ^^type
        try:
            (field, typefield) = field.split('^^')
            if typefield[0] in ["'", '"']:
                typeuri = typefield[1:-1]
            else:
                try:
                    typeuri = _getattr_related(rootobj, obj, [typefield, ] + fields).pop(0)
                except Exception as e:
                    raise ValueError("error accessing data type field '{}' in field '{}' : {}".format(typefield, field, e))
                # have reached end of chain and have used up field list after we hit ^^
                fields = []
        except:
            typeuri = None
        # check for filt
        # check for filter
        if "[" in field:
            filter = field[field.index("[") + 1: -1]
            field = field[0:field.index("[")]

        val = getattr(obj, field)
        if not val:
            return []
        # import pdb; pdb.set_trace()
        try:
            # slice the list for fields[:] to force a copy so each iteration starts from top of list in spite of pop()
            return itertools.chain(*(_getattr_related(rootobj, xx, fields[:]) for xx in val.all()))
        except Exception as e:
            pass
        if filter and not _apply_filter(val, filter, obj, rootobj):
            return []
        if lang:
            val = "@".join((val, lang))
        elif typeuri:
            val = "^^".join((val, typeuri))
    except AttributeError:

        # import pdb; pdb.set_trace()
        filters = _makefilters(filter, obj, rootobj)
        relobjs = _get_relobjs(obj, field, filters)

        # will still throw an exception if val is not set!
    try:
        # slice the list fo fields[:] to force a copy so each iteration starts from top of list in spite of pop()
        return itertools.chain(*(_getattr_related(rootobj, xx, fields[:]) for xx in relobjs.all()))
#        !list(itertools.chain(*([[1],[2]])))
    except:
        return _getattr_related(obj, val, fields)


def _get_relobjs(obj, field, filters):
    """Find related objects that match

    Could be linked using a "related_name" or as <type>_set

    django versions have changed this around so somewhat tricky..
    """
    # then try to find objects of this type with a foreign key property using either (name) supplied or target object type

    if field.endswith(")"):
        (field, relprop) = str(field[0:-1]).split("(")
    else:
        relprop = None

    try:
        reltype = ContentType.objects.get(model=field)
    except ContentType.DoesNotExist as e:
        raise ValueError("Could not locate attribute or related model '{}' in element '{}'".format(field, type(obj)))

    # if no related_name set in related model then only one candidate and djanog creates X_set attribute we can use
    try:
        return get_attr(obj, "".join((field, "_set"))).filter(**filters)
    except:
        pass

    # trickier then - need to look at models of the named type
    claz = reltype.model_class()
    for prop, val in claz.__dict__.items():
        # skip related property names if set
        if relprop and prop != relprop:
            continue
        if relprop or type(val) is ForwardManyToOneDescriptor and val.field.related.model == type(obj):
            filters.update({prop: obj})
            return claz.objects.filter(**filters)


def _makefilters(filter, obj, rootobj):
    """Makes a django filter syntax from provided filter

    allow for filter clauses with references relative to the object being serialised, the root of the path being encoded or the element in the path specifying the filter"""
    if not filter:
        return {}
    filterclauses = dict([fc.split("=") for fc in filter.replace(" AND ", ",").split(",")])
    extrafilterclauses = {}
    for fc in filterclauses:
        fval = filterclauses[fc]
        if not fval:
            extrafilterclauses["".join((fc, "__isnull"))] = False
        elif fval == 'None':
            extrafilterclauses["".join((fc, "__isnull"))] = True
        elif fval.startswith('^'):  # property value via path from root object being serialised
            try:
                objvals = getattr_path(rootobj, fval[1:])
                if len(objvals) == 0:
                    return []  # non null match against null source fails
                extrafilterclauses[fc] = objvals.pop()
            except Exception as e:
                raise ValueError("Error in filter clause %s on field %s " % (fc, prop))

        elif fval.startswith('.'):  # property value via path from current path object
            try:
                objvals = getattr_path(obj, fval[1:])
                if len(objvals) == 0:
                    return []  # non null match against null source fails
                extrafilterclauses[fc] = objvals.pop()
            except Exception as e:
                raise ValueError("Error in filter clause %s on field %s " % (fc, prop))
        elif fval.startswith(("'", '"', '<')):
            extrafilterclauses[fc] = dequote(fval)
        elif not filterclauses[fc].isnumeric():
            # look for a value
            extrafilterclauses[fc] = getattr(obj, fval)
        else:
            extrafilterclauses[fc] = fval

    return extrafilterclauses


def expand_curie(value):
    try:
        parts = value.split(":")
        if len(parts) == 2:
            ns = Namespace.objects.get(prefix=parts[0])
            return "".join((ns.uri, parts[1]))
    except:
        pass
    return value


def validate_urisyntax(value):

    if value[0:4] == 'http':
        URLValidator(verify_exists=False).__call__(value)
    else:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValidationError('invalid syntax')
        ns = Namespace.objects.get(prefix=parts[0])


class CURIE_Field(models.CharField):
    """
        Char field for URI with syntax checking for CURIE or http syntax
    """
    # validate that prefix is registered if used
    validators = [validate_urisyntax, ]

    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 200
        kwargs['help_text'] = _(u'use a:b or full URI')
        super(CURIE_Field, self).__init__(*args, **kwargs)


class EXPR_Field(models.CharField):
    """
        Char field for expression - literal or nested atribute with syntax checking for CURIE or http syntax
    """
    literal_form = None

    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 400
        kwargs['help_text'] = _(u'for a literal, use "quoted" syntax, for nested attribute use syntax a.b.c')
        super(EXPR_Field, self).__init__(*args, **kwargs)


class FILTER_Field(models.CharField):
    """
        Char field for filter expression:  path=value(,value)
    """

    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 400
        kwargs['help_text'] = _(u'path=value, eg label__label_text="frog"')
        super(FILTER_Field, self).__init__(*args, **kwargs)


# Need natural keys so can reference in fixtures - let this be the uri

class NamespaceManager(models.Manager):

    def get_by_natural_key(self, uri):
        return self.get(uri=uri)


class Namespace(models.Model):
    """
        defines a namespace so we can use short prefix where convenient 
    """
    objects = NamespaceManager()

    uri = models.CharField('uri', max_length=100, unique=True, null=False)
    prefix = models.CharField('prefix', max_length=8, unique=True, null=False)
    notes = models.TextField(_(u'change note'), blank=True)

    def natural_key(self):
        return(self.uri)

    def get_base_uri(self):
        return self.uri[0:-1]

    def is_hash_uri(self):
        return self.uri[-1] == '#'

    class Meta:
        verbose_name = _(u'namespace')
        verbose_name_plural = _(u'namespaces')

    def __unicode__(self):
        return self.uri


class GenericMetaPropManager(models.Manager):

    def get_by_natural_key(self, curie):
        try:
            (namespace, prop) = curie.split(":")
        except:
            pass
        return self.get(namespace__prefix=namespace, propname=prop)


class GenericMetaProp(models.Model):
    """
        a metadata property that can be attached to any target model to provide extensible metadata.
        Works with the namespace object to allow short forms of metadata to be displayed
    """
    objects = GenericMetaPropManager()
    namespace = models.ForeignKey(Namespace, verbose_name=_(u'namespace'))
    propname = models.CharField(_(u'name'), blank=False, max_length=250, editable=True)
    definition = models.TextField(_(u'definition'), blank=True)

    def natural_key(self):
        return ":".join((self.namespace.prefix, self.propname))

    def __unicode__(self):              # __unicode__ on Python 2
        return self.natural_key()


class ObjectTypeManager(models.Manager):

    def get_by_natural_key(self, uri):
        return self.get(uri=uri)


class ObjectType(models.Model):
    """
        Allows for a target object to be declared as multiple object types
        Object types may be URI or CURIEs using declared prefixes
    """
    objects = ObjectTypeManager()
    uri = CURIE_Field(_(u'URI'), blank=False, editable=True)
    label = models.CharField(_(u'Label'), blank=False, max_length=250, editable=True)

    def natural_key(self):
        return self.uri

    # check short form is registered
    def __unicode__(self):              # __unicode__ on Python 2
        return " -- ".join((self.uri, self.label))


class ObjectMappingManager(models.Manager):

    def get_by_natural_key(self, name):
        return self.get(name=name)


class ObjectMapping(models.Model):
    """
        Maps an instance of a model to a resource (i.e. a URI with a type declaration) 
    """
    objects = ObjectMappingManager()
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    name = models.CharField(_(u'Name'), help_text=_(u'unique identifying label'), unique=True, blank=False, max_length=250, editable=True)
    auto_push = models.BooleanField(_(u'auto_push'), help_text=_(u'set this to push updates to these object to the RDF store automatically'))
    id_attr = models.CharField(_(u'ID Attribute'), help_text=_(u'for nested attribute use syntax a.b.c'), blank=False, max_length=250, editable=True)
    target_uri_expr = EXPR_Field(_(u'target namespace expression'), blank=False, editable=True)
    obj_type = models.ManyToManyField(ObjectType, null=True, blank=True, help_text=_(u'set this to generate a object rdf:type X statement'))
    filter = FILTER_Field(_(u'Filter'), null=True, blank=True, editable=True)

    def natural_key(self):
        return self.name

    def __unicode__(self):              # __unicode__ on Python 2
        return self.name


class AttributeMapping(models.Model):
    """
        records a mapping from an object mapping that defines a relation from the object to a value using a predicate
    """
    scope = models.ForeignKey(ObjectMapping)
    attr = EXPR_Field(_(u'source attribute'), help_text=_(u'literal value or path (attribute[filter].)* with optional @element or ^^element eg locationname[language=].name@language.  filter values are empty (=not None), None, or a string value'), blank=False, editable=True)
    # filter = FILTER_Field(_(u'Filter'), null=True, blank=True,editable=True)
    predicate = CURIE_Field(_(u'predicate'), blank=False, editable=True)
    is_resource = models.BooleanField(_(u'as URI'))

    def __unicode__(self):
        return (' '.join((self.attr, self.predicate)))


class EmbeddedMapping(models.Model):
    """
        records a mapping for a complex data structure
    """
    scope = models.ForeignKey(ObjectMapping)
    attr = EXPR_Field(_(u'source attribute'), help_text=_(u'attribute - if empty nothing generated, if multivalued will be iterated over'))
    predicate = CURIE_Field(_(u'predicate'), blank=False, editable=True)
    struct = models.TextField(_(u'object structure'), max_length=2000, help_text=_(u' ";" separated list of <em>predicate</em> <em>attribute expr</em>  where attribute expr a model field or "literal" or <uri> - in future may be an embedded struct inside {} '), blank=False, editable=True)
    use_blank = models.BooleanField(_(u'embed as blank node'), default=True)

    def __unicode__(self):
        return (' '.join(('struct:', self.attr, self.predicate)))
